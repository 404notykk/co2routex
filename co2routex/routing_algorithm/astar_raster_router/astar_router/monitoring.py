"""Sample process RSS, including native raster/NumPy allocations."""
import threading
import psutil


class MemoryMonitor:
    def __init__(self, interval=0.05):
        self.interval = interval
        self.process = psutil.Process()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.peak = 0.0
        self.pair_peak = None

    def sample(self):
        with self.lock:
            rss = self.process.memory_info().rss / 2**20
            self.peak = max(self.peak, rss)
            if self.pair_peak is not None:
                self.pair_peak = max(self.pair_peak, rss)
            return rss

    def _loop(self):
        while not self.stop_event.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def begin_pair(self):
        with self.lock:
            rss = self.process.memory_info().rss / 2**20
            self.pair_peak = rss
            self.peak = max(self.peak, rss)
            return rss

    def end_pair(self):
        self.sample()
        with self.lock:
            value = self.pair_peak
            self.pair_peak = None
            return value

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join()
        self.sample()
