
from text_utils import slugify

def make_url(title):
    return "/posts/" + slugify(title)
