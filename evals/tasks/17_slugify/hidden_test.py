
from app import make_url
def test_url(): assert make_url("Hello World!") == "/posts/hello-world"
def test_numbers(): assert make_url("Top 10 Tips") == "/posts/top-10-tips"
