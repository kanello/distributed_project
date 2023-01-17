from flask import Flask # type: ignore

from node import Node
from flask_bindings import bind

"""
Encapsulates the management of the flask app.  
This includes initializing the server, deciding when to bind endpoints, and starting/stopping the server.
"""

class FlaskServer:
    def __init__(self, ip: str, port: int, node: Node):
        self.ip: str = ip
        self.port: int = port

        # stores all state related to the message queue as well as implement's Raft's algorithm
        self.node: Node = node
        self.app: Flask = None

        self.initializeAndStartFlaskServer()

    def initializeAndStartFlaskServer(self):
        app = Flask("Flask: Message Server")
        bind(app, self.node) # binds the routes to the flask server

        self.app = app

    def startFlaskServer(self):
        print(f'Starting flask server on {self.ip}:{self.port}')
        self.app.run(debug=False, host=self.ip, port=self.port, threaded=True)