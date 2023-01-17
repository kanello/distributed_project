"""
Binds rest api endpoints to a Flask server
"""

import json
from typing import Any, Dict, List, Tuple
from flask import Flask, jsonify, request, Response # type: ignore
from helpers import ErrorCode, NodeOperationResult

from node import Node # type: ignore


"""
flask_app_instance: a Flask app that we want to bind these endpoints to
node: a Node instance which encapsulates all the data for our distributed message queue
binds all the rest endpoints to the flask app
"""

# On request failures, we might want to provide additional details to the client.
# represents a message returned by the server
# message = lambda x: {"message": x} 

ServerResponse = Tuple[Dict[str, Any], int]
getServerResponse = lambda response_body, status_code: (response_body, status_code)

def bind(flask_app_instance: Flask, node: Node):
    # maps errors encountered during Node operations to http status codes
    errors: Dict[ErrorCode, int] = {
            ErrorCode.LEADER_ERROR: 302,
            ErrorCode.REQUESTED_MESSAGE_FROM_TOPIC_THAT_DOES_NOT_EXIST_ERROR: 404,
            ErrorCode.QUEUE_EMPTY_ERROR: 404,
            ErrorCode.REQUESTED_TO_WRITE_MESSAGE_TO_TOPIC_THAT_DOES_NOT_EXIST_ERROR: 404,
            ErrorCode.COULD_NOT_RESOLVE_STATE: 500,
            ErrorCode.REQUESTED_TO_ADD_TOPIC_WHICH_ALREADY_EXISTS_ERROR: 200
            }

    #---------------------------------------------------------------------------------------
    # TOPICS
    @flask_app_instance.route('/topic', methods=['GET'])
    def get_topics() -> ServerResponse:
        """
        Get a list of all topics

        Returns
        -------
        success: True
        topics: list
            a list of available topics
        """
        result: NodeOperationResult = node.get_topics()

        # 302 indicates Found: The URL of the requested resource has been changed temporarily. 
        # i.e. you have requested the topics from a node which is not the leader.
        code = 200 if result.success else 302

        # not the biggest fan of using dictionary literals like this because it introduces tight coupling
        # between here and server.py. Bugs related to typo's can come up and these cannot be caught statically.
        # It's not that big of a deal though because we know for a fact this interface will not be extended since it's a final project.
        topics = result.data["topics"] if result.data is not None and result.success else []

        return getServerResponse({"success": result.success, "topics": topics}, code)

    @flask_app_instance.route('/topic', methods=['PUT'])
    def create_topic() -> ServerResponse:
        """
        Create a topic.
        Creates a FIFO queue in the message_queue dict under the topic name

        Returns
        -------
        success: bool
            True if a topic was added to the message_queue dict and to the topics list
            False if the topic already exists
        """
        try:
            topic: str = request.json["topic"]
        except Exception:
            # client failed to provide a topic
            return getServerResponse({"success": False}, 400)

        result: NodeOperationResult = node.put_topic(topic)
        if result.success:
            code = 201
        else:
            code = errors.get(result.error if result.error is not None else ErrorCode.MISC_ERROR, 500)

        return getServerResponse({"success": result.success}, code)

    #---------------------------------------------------------------------------------------
    # MESSAGES
    @flask_app_instance.route('/message/<topic>', methods=['GET'])
    def get_message(topic) -> ServerResponse:
        """
        Gets the first message in the requested topic, in FIFO order

        Returns
        -------
        success: bool
            True is there was a message to be returned for the requested topic
            False if the topic does not exist or there is no message to be returned
        message: str
            the first message that is in the message queue for the given topic
        """
        result: NodeOperationResult = node.get_message(topic)

        code = 200 if result.success else errors.get(result.error if result.error is not None else ErrorCode.MISC_ERROR, 500)
        message = result.data["message"] if result.success and result.data is not None else ""
        data = {"success":result.success, "message":message} if result.success else  {"success":result.success}


        return getServerResponse(data, code)

    @flask_app_instance.route('/message', methods=['PUT'])
    def write_message() -> ServerResponse:
        """
        Write message to an existing topic

        Returns
        -------
        success: bool
            True if a message was written to an existing topic
            False if the requested topic doesn't exist; message not written
        """
        try:
            payload: str = request.json
            message: str = payload["message"]
            topic: str = payload["topic"]
        except:
            return getServerResponse({"success": False}, 400)

        result: NodeOperationResult = node.write_message(topic, message)
        code = 201 if result.success else errors.get(result.error if result.error is not None else ErrorCode.MISC_ERROR, 500)

        return getServerResponse({"success": result.success}, code)

    #---------------------------------------------------------------------------------------
    # STATUS
    @flask_app_instance.route('/status', methods=['GET'])
    def get_status() -> ServerResponse:
        """
        Get status from each candidate type.
        """
        result: NodeOperationResult = node.get_status()

        if result.data:
            return getServerResponse({"role": result.data["role"], "term": result.data["term"]}, 200)
        else:
            return getServerResponse({"role": "unresolved_role", "term": -1}, 500)