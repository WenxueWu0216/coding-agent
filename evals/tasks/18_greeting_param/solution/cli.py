
from greetings import greet

def welcome(name, greeting="Hello"):
    return greet(name.strip().title(), greeting)
