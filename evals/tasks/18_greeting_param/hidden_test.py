
from cli import welcome
from greetings import greet
def test_default(): assert welcome(" ana ") == "Hello, Ana!"
def test_custom(): assert welcome("ana", greeting="Hi") == "Hi, Ana!"
def test_greet(): assert greet("Bo", "Hey") == "Hey, Bo!"
