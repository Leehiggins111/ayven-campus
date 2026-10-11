import pytest
from app.intelligence.planner import classify
from app.intelligence.execution import _calculation_from_objective

@pytest.mark.parametrize('objective,expected', [
    ('multiply six by seven', '42.00'), ('what is six multiplied by seven?', '42.00'),
    ("what's six times seven?", '42.00'), ('can you tell me what 6 x 7 equals?', '42.00'),
    ('tell me the product of six and seven', '42.00'), ('add twenty-one to seven', '28.00'),
    ('subtract three from twelve', '9.00'), ('divide twelve by three', '4.00'),
    ('what is -6 * 7', '-42.00'), ('Calculate (6 + 7) * 2', '26.00'),
    ('what is 6 x 7', '42.00'), ('What is 6 X 7?', '42.00'),
    ('what is 6 times 7', '42.00'), ('6 × 7', '42.00'),
    ('Calculate 6 * 7', '42.00'), ('what is 12 divided by 3', '4.00'),
    ('What is 1.5 plus 2.5?', '4.00'), ('what is the sum of 6 and 7', '13.00'),
])
def test_natural_arithmetic_uses_verified_calculator(objective, expected):
    assert classify(objective) == 'calculation'
    assert _calculation_from_objective(objective)['value'] == expected

def test_short_greeting_and_domain_work_keep_their_routes():
    assert classify('hello') == 'trivial'
    assert classify('Quote internal doors 6 x 7') == 'internal_door_quote'
    assert classify('Meeting on 6/7') != 'calculation'

@pytest.mark.parametrize('objective', ['what is six point five plus two', 'what is one hundred and five plus two', 'what is six seven plus one', 'Calculate 1 / 0'])
def test_unparsed_arithmetic_is_not_partially_answered(objective):
    assert classify(objective) == 'calculation'
    assert _calculation_from_objective(objective) is None
