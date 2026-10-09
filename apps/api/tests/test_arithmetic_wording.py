import pytest
from app.intelligence.planner import classify
from app.intelligence.execution import _calculation_from_objective

@pytest.mark.parametrize('objective,expected', [
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
