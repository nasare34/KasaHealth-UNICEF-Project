"""Concurrent users through the full stack (browser-facing proxy → API → mock upstreams)."""
import asyncio, sys, time, httpx

async def main(n, distinct):
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=n)) as c:
        async def one(i):
            msg = f"Question number {i}" if distinct else "What is puberty?"
            t = time.perf_counter()
            r = await c.post("http://localhost:5000/api/chat", json={"message": msg, "language": "tw"})
            return r.status_code, time.perf_counter() - t
        t0 = time.perf_counter()
        res = await asyncio.gather(*[one(i) for i in range(n)])
        total = time.perf_counter() - t0
    codes = {}
    for code, _ in res: codes[code] = codes.get(code, 0) + 1
    lat = sorted(l for _, l in res)
    print(f"{n} concurrent {'distinct' if distinct else 'identical'} questions: total {total:.2f}s, "
          f"p50 {lat[n//2]:.2f}s, p95 {lat[int(n*0.95)-1]:.2f}s, statuses {codes}")

asyncio.run(main(int(sys.argv[1]), sys.argv[2] == "distinct"))
