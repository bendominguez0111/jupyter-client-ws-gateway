import tornado.ioloop
import tornado.web
import tornado.websocket
import logging
from jupyter_client import KernelManager
import json
import datetime
import os
from queue import Empty

# set up basic logging to std out
logging.basicConfig(level=logging.INFO)

auth = os.getenv("WS_TOKEN", "secret")


# setting up secrets
class DatetimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)


class JupyterWebSocketHandler(tornado.websocket.WebSocketHandler):
    def initialize(self, kernel_manager):
        self.kernel_manager = kernel_manager
        self.km = None
        self.kc = None

    def open(self):
        # Create kernel
        logging.info(f"WebSocket opened by {self.request.remote_ip}")
        token = self.get_argument("token", default=None, strip=False)
        self.validate_token(token)

        self.km = self.kernel_manager()
        self.km.start_kernel()
        self.kc = self.km.client(blocking=False)
        self.kc.start_channels()

    def validate_token(self, token):
        if token != auth:
            raise tornado.web.HTTPError(403)

    def on_message(self, message):
        # Execute the message in the kernel and store the execution UUID
        self.uuid = self.kc.execute(message)

        # Poll iopub channel for messages
        tornado.ioloop.IOLoop.current().add_timeout(
            datetime.timedelta(seconds=0.1),
            lambda: self.poll_msg_channel("iopub", self.kc.get_iopub_msg),
        )
        tornado.ioloop.IOLoop.current().add_timeout(
            datetime.timedelta(seconds=0.1),
            lambda: self.poll_msg_channel("shell", self.kc.get_shell_msg),
        )
        tornado.ioloop.IOLoop.current().add_timeout(
            datetime.timedelta(seconds=0.1),
            lambda: self.poll_msg_channel("stdin", self.kc.get_stdin_msg),
        )
        tornado.ioloop.IOLoop.current().add_timeout(
            datetime.timedelta(seconds=0.1),
            lambda: self.poll_msg_channel("control", self.kc.get_control_msg),
        )

    def poll_msg_channel(self, channel, poll_channel_func, timeout=0.1):
        while True:
            try:
                msg = poll_channel_func(timeout=timeout)
                if not msg["parent_header"]:
                    # likely a status message
                    continue
                if msg["parent_header"]["msg_id"] == self.uuid:
                    msg["channel"] = channel
                    json_reply = json.dumps(msg, cls=DatetimeEncoder)
                    self.write_message(json_reply)
            except Empty:
                break
            except tornado.websocket.WebSocketClosedError:
                break
            except Exception as e:
                logging.error(e)

    def check_origin(self, _):
        return True

    def on_close(self):
        # When the connection is closed, shut down the kernel
        logging.info(f"WebSocket closed by {self.request.remote_ip}")
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            self.km.shutdown_kernel()


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("index.html")


def make_app():
    kernel_manager = KernelManager
    return tornado.web.Application(
        [
            (r"/", MainHandler),
            (r"/ws", JupyterWebSocketHandler, dict(kernel_manager=kernel_manager)),
        ],
    )


if __name__ == "__main__":
    app = make_app()
    app.listen(8888)
    logging.info("Listening on port 8888")
    tornado.ioloop.IOLoop.current().start()
