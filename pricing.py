# USD per million tokens (input, output), from the Claude pricing page
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}


def cost_usd(model, input_tokens, output_tokens):
    p_in, p_out = PRICES.get(model, (0, 0))
    return input_tokens / 1e6 * p_in + output_tokens / 1e6 * p_out
