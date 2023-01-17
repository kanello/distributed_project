"""
Light weight file to test flask api endpoints
"""

import json
import requests
import sys

ports = [str(sys.argv[1])]
# ports = ["5080"]
base_url = 'http://127.0.0.1:'

def formatResponse(response):
    return f'response received. code: {response.status_code}, content: {response.content}'

# should receive one status code of 200 and the rest should be 302 (requested topics from a non-leader)
def test_get_topics():
    print("Test Get Topic")
    for p in ports:
        url = f'{base_url}{p}/topic'
        r = requests.get(url=url)
        print(formatResponse(r))

def test_put_topic(topic: str):
    print("Test Put Topic")
    for p in ports:
        url = f'{base_url}{p}/topic'
        r = requests.put(url=url, json={"topic": topic})
        print(r.json())
        

def test_get_message(topic: str):
    print("Test Get Message")
    for p in ports:
        url = f'{base_url}{p}/message/{topic}'
        r = requests.get(url=url)
        print(formatResponse(r))

def test_put_message(topic: str, message: str):
    print("Test Put Message")
    for p in ports:
        url = f'{base_url}{p}/message'
        r = requests.put(url=url, json = {"topic": topic, "message": message})
        print(r.json())


def test_get_status():
    print("Test Get Status")
    for p in ports:
        url = f'{base_url}{p}/status'
        r = requests.get(url=url)
        print(p + ":" + formatResponse(r))

topic = "hello_world_topic_1"
test_message = "testC"

# test_get_status()
# test_get_topics()

# test_put_topic(topic)
# test_get_topics()

test_put_message(topic, test_message)
# test_get_message(topic)

# test_get_status()