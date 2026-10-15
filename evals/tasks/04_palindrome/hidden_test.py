
from text import is_palindrome
def test_phrase(): assert is_palindrome("A man, a plan, a canal: Panama")
def test_no(): assert not is_palindrome("hello")
def test_case(): assert is_palindrome("Racecar")
