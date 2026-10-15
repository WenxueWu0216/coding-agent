
from email_check import is_valid_email
def test_good(): assert is_valid_email("xuexue@stevens.edu")
def test_bad():
    for s in ["a@b", "@x.com", "a@@x.com", "a b@x.com", "a@.com", "a@x.com."]:
        assert not is_valid_email(s), s
