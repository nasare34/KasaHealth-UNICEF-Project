# End-to-end tests

Runs the real backend and frontend (Gunicorn, production settings) against a
mock of xAI, Khaya, Google Forms and Hugging Face, then drives the app in a
headless browser with a fake microphone.

```bash
pip install playwright && python -m playwright install chromium
python make_speech.py                         # fake mic audio
./start_stack.sh                              # start everything
python browser_test.py                        # 23 UI checks incl. voice agent
python browser_test_v81.py                    # 26 checks: themes, sidebar, safety, admin, hedging, mobile
./start_stack.sh && python browser_test_admin.py   # 37 checks: health dashboard, advice, scrolling
RATE_LIMIT_AI_PER_MIN=100000 ./start_stack.sh && python load_test.py 100 distinct
./start_stack.sh stop
```
