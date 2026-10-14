"""Read one network block at a time without letting trickle traffic defeat deadlines."""
import time

from .errors import DeadlineExceeded


def read_block(response, size, *, deadline, timeout, clock=time.monotonic,
               message='MVSEP response time limit reached. The saved job can be resumed.'):
    remaining = deadline - clock()
    if remaining <= 0:
        raise DeadlineExceeded(message)
    # urllib returns an HTTPResponse, whose buffered socket otherwise keeps the
    # timeout from open(). Shorten it as the total budget runs out. HTTPError
    # wraps that response in fp; BytesIO/test responses need no socket timeout.
    stream = response
    if not callable(getattr(stream, 'read1', None)):
        stream = getattr(response, 'fp', None) or response
    sock = getattr(getattr(getattr(stream, 'fp', None), 'raw', None), '_sock', None)
    if sock is not None:
        sock.settimeout(min(timeout, remaining))
    # HTTPResponse.read(n) can perform many recv() calls, each resetting the
    # socket timeout. read1(n) returns after one buffered/network read instead.
    reader = getattr(stream, 'read1', None) or stream.read
    block = reader(size)
    if clock() >= deadline:
        raise DeadlineExceeded(message)
    return block


def read_limited(response, limit, *, deadline, timeout, clock=time.monotonic):
    result = bytearray()
    while len(result) < limit:
        block = read_block(response, min(64 * 1024, limit - len(result)),
                           deadline=deadline, timeout=timeout, clock=clock)
        if not block:
            break
        result.extend(block)
    return bytes(result)
