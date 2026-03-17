"""Tests for ETA estimation logic."""

import time

import build_pdfium as bp


class TestEstimateRemainingReturnsNone:
    """Cases where we don't have enough data to estimate."""

    def test_no_phase_start(self):
        assert bp._estimate_remaining(5, 100, 0, None, 0.5, 10, time.time()) is None

    def test_insufficient_elapsed_time(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=10,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 5,  # only 5s < 10s threshold
            ema_secs_per_step=0.5,
            ema_samples=20,
            now=now,
        )
        assert result is None

    def test_insufficient_steps(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=3,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 30,  # enough time
            ema_secs_per_step=0.5,
            ema_samples=20,
            now=now,
        )
        assert result is None


class TestEstimateRemainingNoEma:
    """When all steps were COPY/CACHED and EMA has no data."""

    def test_returns_prior_based_estimate(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=10,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 30,
            ema_secs_per_step=None,
            ema_samples=0,
            now=now,
        )
        assert result is not None
        assert result > 3000  # should be close to ETA_INITIAL_SECS - 30

    def test_prior_decays_with_elapsed(self):
        now = time.time()
        early = bp._estimate_remaining(
            step=10,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 30,
            ema_secs_per_step=None,
            ema_samples=0,
            now=now,
        )
        late = bp._estimate_remaining(
            step=10,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 600,
            ema_secs_per_step=None,
            ema_samples=0,
            now=now,
        )
        assert late < early


class TestEstimateRemainingBlending:
    """Test the prior-to-observed blending as EMA samples accumulate."""

    def test_low_confidence_near_prior(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=10,
            total=2000,
            phase_start_step=0,
            phase_start_time=now - 15,
            ema_secs_per_step=0.5,
            ema_samples=5,
            now=now,
        )
        # With 5/60 samples, should be mostly prior (~58 min)
        assert result > 3000

    def test_full_confidence_matches_observed(self):
        now = time.time()
        remaining_steps = 2000 - 200
        ema_rate = 0.5
        result = bp._estimate_remaining(
            step=200,
            total=2000,
            phase_start_step=0,
            phase_start_time=now - 120,
            ema_secs_per_step=ema_rate,
            ema_samples=60,
            now=now,
        )
        expected = ema_rate * remaining_steps
        assert abs(result - expected) < 1.0

    def test_over_confidence_clamped(self):
        now = time.time()
        at_60 = bp._estimate_remaining(
            step=200,
            total=2000,
            phase_start_step=0,
            phase_start_time=now - 120,
            ema_secs_per_step=0.5,
            ema_samples=60,
            now=now,
        )
        at_120 = bp._estimate_remaining(
            step=200,
            total=2000,
            phase_start_step=0,
            phase_start_time=now - 120,
            ema_secs_per_step=0.5,
            ema_samples=120,
            now=now,
        )
        assert abs(at_60 - at_120) < 1.0

    def test_monotonic_convergence(self):
        """As samples increase, estimate should move toward observed."""
        now = time.time()
        estimates = []
        for samples in [5, 15, 30, 45, 60]:
            r = bp._estimate_remaining(
                step=100,
                total=2000,
                phase_start_step=0,
                phase_start_time=now - 60,
                ema_secs_per_step=0.5,
                ema_samples=samples,
                now=now,
            )
            estimates.append(r)
        # Should be monotonically decreasing toward observed
        for i in range(len(estimates) - 1):
            assert estimates[i] >= estimates[i + 1]


class TestEstimateRemainingEdgeCases:
    def test_never_negative(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=99,
            total=100,
            phase_start_step=0,
            phase_start_time=now - 5000,
            ema_secs_per_step=0.001,
            ema_samples=60,
            now=now,
        )
        assert result >= 0

    def test_near_completion(self):
        now = time.time()
        result = bp._estimate_remaining(
            step=1999,
            total=2000,
            phase_start_step=0,
            phase_start_time=now - 900,
            ema_secs_per_step=0.5,
            ema_samples=60,
            now=now,
        )
        assert result < 2.0  # 0.5 * 1 = 0.5s
