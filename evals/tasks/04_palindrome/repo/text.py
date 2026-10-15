
def is_palindrome(s):
    cleaned = [c for c in s if c.isalpha()]
    return cleaned == cleaned[::-1]
