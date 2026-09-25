"""Offline check that encode_for_generate never returns a mapping as positional generate input."""
from validation.harness import encode_for_generate

class T:
    def __init__(self, n=8, d=2):
        self._n, self._d = n, d
    def to(self, device):
        return self
    def dim(self):
        return self._d
    def unsqueeze(self, i):
        return T(self._n, self._d + 1)
    @property
    def shape(self):
        return (1, self._n)

class FakeTok:
    def apply_chat_template(self, *a, **k):
        return {"input_ids": T(), "attention_mask": T()}

def test_batchencoding_like():
    ids, kw = encode_for_generate(FakeTok(), [], "cpu")
    assert "input_ids" in kw
    assert hasattr(kw["input_ids"], "shape")
    print("ok", list(kw))

if __name__ == "__main__":
    test_batchencoding_like()
