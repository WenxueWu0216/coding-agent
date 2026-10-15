
from words import top_words
def test_basic(): assert top_words("the cat and the hat. The end!", 2) == [("the", 3), ("and", 1)]
def test_ties(): assert top_words("b a c", 3) == [("a", 1), ("b", 1), ("c", 1)]
