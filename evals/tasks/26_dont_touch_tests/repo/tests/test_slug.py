
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slug import slugify

def test_basic():
    assert slugify("Hello World") == "hello-world"

def test_accents():
    assert slugify("Crème Brûlée") == "creme-brulee"

def test_punctuation_and_spaces():
    assert slugify("  Rock & Roll!!  ") == "rock-roll"

def test_collapse_dashes():
    assert slugify("a -- b") == "a-b"
