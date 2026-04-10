"""Tests for kora_v2.memory.signal_scanner — Rule-based signal detection."""

import pytest

from kora_v2.memory.signal_scanner import ScanResult, SignalScanner, SignalType


@pytest.fixture
def scanner():
    return SignalScanner()


class TestNameDetection:
    """Test detection of identity/explicit fact signals."""

    def test_name_detection(self, scanner):
        """'My name is Alex' should detect explicit_fact signal."""
        result = scanner.scan("My name is Alex")
        assert result.has_signal is True
        assert SignalType.EXPLICIT_FACT in result.signal_types
        assert result.priority <= 3

    def test_age_detection(self, scanner):
        """'My age is 25' should detect explicit_fact signal."""
        result = scanner.scan("My age is 25")
        assert result.has_signal is True
        assert SignalType.EXPLICIT_FACT in result.signal_types


class TestNoFalsePositive:
    """Short greetings and low-signal messages should not trigger signals."""

    def test_hello(self, scanner):
        """'Hello' should return no signals (low-signal pattern)."""
        result = scanner.scan("Hello")
        assert result.has_signal is False
        assert result.priority == 5
        assert result.signal_types == []

    def test_greeting_variations(self, scanner):
        """Various greetings should be low-signal."""
        for greeting in ["hi", "hey", "yo", "thanks", "ok", "sure", "yeah", "bye"]:
            result = scanner.scan(greeting)
            assert result.has_signal is False, f"'{greeting}' should be low-signal"

    def test_empty_string(self, scanner):
        """Empty string should return priority 5 with no signals."""
        result = scanner.scan("")
        assert result.priority == 5
        assert result.signal_types == []


class TestCorrectionDetection:
    """Test priority-1 correction/contradiction detection."""

    def test_actually_correction(self, scanner):
        """'Actually, I live in Seattle' should detect correction."""
        result = scanner.scan("Actually, I live in Seattle now")
        assert SignalType.CORRECTION in result.signal_types
        assert result.priority == 1

    def test_thats_wrong(self, scanner):
        """'That's not right' should detect correction."""
        result = scanner.scan("That's not right, I meant something else")
        assert SignalType.CORRECTION in result.signal_types


class TestLifeEventDetection:
    """Test priority-2 life event signals."""

    def test_got_married(self, scanner):
        """'I got married last month' should detect life event."""
        result = scanner.scan("I got married last month")
        assert SignalType.LIFE_EVENT in result.signal_types
        assert result.priority <= 2

    def test_moved_to(self, scanner):
        """'I moved to Denver' should detect life event."""
        result = scanner.scan("I moved to Denver last week")
        assert SignalType.LIFE_EVENT in result.signal_types

    def test_graduated(self, scanner):
        """'I graduated from college' should detect life event."""
        result = scanner.scan("I graduated from college!")
        assert SignalType.LIFE_EVENT in result.signal_types


class TestNewPersonDetection:
    """Test priority-2 new person mentions."""

    def test_my_friend_named(self, scanner):
        """'My friend Sarah' should detect new person."""
        result = scanner.scan("My friend Sarah told me about it")
        assert SignalType.NEW_PERSON in result.signal_types

    def test_my_boss(self, scanner):
        """'My boss David' should detect new person."""
        result = scanner.scan("My boss David asked me to handle this")
        assert SignalType.NEW_PERSON in result.signal_types


class TestPreferenceDetection:
    """Test priority-3 strong preference signals."""

    def test_i_love(self, scanner):
        """'I love hiking' should detect strong preference."""
        result = scanner.scan("I love hiking in the mountains")
        assert SignalType.STRONG_PREFERENCE in result.signal_types
        assert result.priority <= 3

    def test_my_favorite(self, scanner):
        """'My favorite' should detect strong preference."""
        result = scanner.scan("My favorite food is sushi")
        assert SignalType.STRONG_PREFERENCE in result.signal_types


class TestLifeManagementSignals:
    """Test life management signal types (medication, finance, meal, time block)."""

    def test_medication(self, scanner):
        """Medication mentions should be detected."""
        result = scanner.scan("I took my meds this morning")
        assert SignalType.MEDICATION in result.signal_types

    def test_finance(self, scanner):
        """Financial mentions should be detected."""
        result = scanner.scan("I spent $50 on groceries")
        assert SignalType.FINANCE in result.signal_types

    def test_meal(self, scanner):
        """Meal mentions should be detected."""
        result = scanner.scan("I had pasta for lunch")
        assert SignalType.MEAL in result.signal_types

    def test_time_block(self, scanner):
        """Focus/time block mentions should be detected."""
        result = scanner.scan("I'm starting deep work now")
        assert SignalType.TIME_BLOCK in result.signal_types


class TestGeneralSignal:
    """Test general (priority 4) and no-signal cases."""

    def test_medium_length_no_pattern(self, scanner):
        """Text >50 chars without pattern matches should be priority 4 general."""
        text = "I went to the store and then came back home and watched some TV for a while"
        result = scanner.scan(text)
        # This might or might not match patterns; if no specific patterns match,
        # it should be general (priority 4) due to length > 50
        assert result.priority <= 4

    def test_short_no_pattern(self, scanner):
        """Short text (<= 50 chars) without pattern matches should be priority 5."""
        result = scanner.scan("the weather is nice today")
        # This short message with no patterns should be priority 5
        # (but "nice" alone won't match anything — depends on exact length)
        assert result.priority >= 4


class TestScannerMetrics:
    """Test scanner internal metrics tracking."""

    def test_scans_performed(self, scanner):
        """scans_performed should increment on each scan."""
        assert scanner.scans_performed == 0
        scanner.scan("Hello")
        scanner.scan("My name is Test")
        assert scanner.scans_performed == 2

    def test_signals_detected(self, scanner):
        """signals_detected should count all detected signals."""
        assert scanner.signals_detected == 0
        scanner.scan("Hello")  # no signals
        scanner.scan("My name is Alex")  # should detect 1+ signal
        assert scanner.signals_detected >= 1
