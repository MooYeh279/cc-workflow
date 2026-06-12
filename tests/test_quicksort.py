import pytest
from wflow.quicksort import quicksort


class TestQuicksort:
    def test_empty_list(self):
        assert quicksort([]) == []

    def test_single_element(self):
        assert quicksort([42]) == [42]

    def test_already_sorted(self):
        assert quicksort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]

    def test_reverse_sorted(self):
        assert quicksort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]

    def test_duplicates(self):
        assert quicksort([3, 1, 2, 1, 3]) == [1, 1, 2, 3, 3]

    def test_negative_numbers(self):
        assert quicksort([-3, 0, 5, -1, 2]) == [-3, -1, 0, 2, 5]

    def test_strings(self):
        assert quicksort(["banana", "apple", "cherry"]) == ["apple", "banana", "cherry"]

    def test_with_key_function(self):
        result = quicksort([(3, "c"), (1, "a"), (2, "b")], key=lambda x: x[0])
        assert result == [(1, "a"), (2, "b"), (3, "c")]

    def test_with_key_len(self):
        result = quicksort(["aaa", "a", "aa"], key=len)
        assert result == ["a", "aa", "aaa"]

    def test_original_list_is_modified(self):
        arr = [3, 1, 2]
        quicksort(arr)
        assert arr == [1, 2, 3]

    def test_large_list(self):
        arr = list(range(1000, 0, -1))
        assert quicksort(arr) == list(range(1, 1001))
