#!/usr/bin/env python3
"""
Golden Navratna Kuber — Central Lottery & Admin Server
Zero-dependency Python 3 standard library server.
Runs the authoritative 15-minute lottery draw engine, manages live state,
provides REST API for public site & admin panel, and serves web files.
"""

import http.server
import socketserver
import json
import os
import sys
import time
import random
import threading
from datetime import datetime, timezone
import urllib.parse

PORT = 8000
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'lottery_state.json')

DEFAULT_COUPONS = [
    {"name": "NAVRATNA",  "win": 100, "latest": 63},
    {"name": "CHETAK",    "win": 100, "latest": 30},
    {"name": "RAJA RANI", "win": 100, "latest": 55},
]

DRAW_INTERVAL_MINS = 15

state_lock = threading.Lock()
state = {}


def calc_next_draw_timestamp(now_ts=None):
    if now_ts is None:
        now_ts = time.time()
    dt = datetime.fromtimestamp(now_ts)
    mins = dt.minute
    next_mins = ((mins // DRAW_INTERVAL_MINS) + 1) * DRAW_INTERVAL_MINS
    
    # Calculate target datetime
    target_hour = dt.hour + (next_mins // 60)
    target_min = next_mins % 60
    target_dt = dt.replace(hour=target_hour % 24, minute=target_min, second=0, microsecond=0)
    if target_hour >= 24 or target_dt.timestamp() <= now_ts:
        # Crosses to next day
        target_ts = (dt.replace(minute=0, second=0, microsecond=0).timestamp() 
                     + (dt.hour * 3600) + (next_mins * 60))
        # Better: simply compute from current timestamp
        current_minute_start = dt.replace(minute=0, second=0, microsecond=0).timestamp()
        target_ts = current_minute_start + (next_mins * 60)
        return target_ts
    return target_dt.timestamp()


def format_time_12h(ts):
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%I:%M %p")


def format_date(ts):
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%m/%d/%Y")


def load_or_init_state():
    global state
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                # Ensure fields exist
                if 'coupons' not in state or not state['coupons']:
                    state['coupons'] = [dict(c) for c in DEFAULT_COUPONS]
                if 'overrides' not in state:
                    state['overrides'] = {}
                if 'history' not in state:
                    state['history'] = []
                if 'next_draw_ts' not in state or state['next_draw_ts'] <= time.time():
                    state['next_draw_ts'] = calc_next_draw_timestamp()
                return
        except Exception as e:
            print(f"[WARN] Failed to load state: {e}. Reinitializing.")

    now_ts = time.time()
    state = {
        "coupons": [dict(c) for c in DEFAULT_COUPONS],
        "next_draw_ts": calc_next_draw_timestamp(now_ts),
        "overrides": {},
        "latest_draw": {
            "id": f"D-{int(now_ts) % 1000000:06d}",
            "time": format_time_12h(now_ts),
            "date": format_date(now_ts),
            "timestamp": int(now_ts * 1000),
            "results": {c['name']: c['latest'] for c in DEFAULT_COUPONS},
            "type": "initial"
        },
        "history": []
    }
    save_state()


def save_state():
    try:
        tmp_file = STATE_FILE + ".tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        print(f"[ERROR] Could not save state: {e}")


def get_slot_auto_number(coupon_name, slot_key):
    s = f"{coupon_name}:{slot_key}"
    h = 0
    for ch in s:
        h = ((h << 5) - h) + ord(ch)
        h = h & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(h) % 100


def execute_draw(draw_type="auto", custom_overrides=None):
    with state_lock:
        now_ts = time.time()
        time_str = format_time_12h(now_ts)
        date_str = format_date(now_ts)

        dt = datetime.fromtimestamp(now_ts)
        slot_minute = (dt.minute // DRAW_INTERVAL_MINS) * DRAW_INTERVAL_MINS
        slot_key = f"{dt.year}-{dt.month}-{dt.day} {dt.hour}:{slot_minute}"
        
        # Use custom overrides if provided, else use queued overrides
        overrides = custom_overrides if custom_overrides is not None else state.get("overrides", {})
        results = {}

        for coupon in state["coupons"]:
            cname = coupon["name"]
            if cname in overrides and overrides[cname] is not None and str(overrides[cname]).strip() != "":
                try:
                    num = int(overrides[cname])
                    if not (0 <= num <= 99):
                        num = get_slot_auto_number(cname, slot_key)
                except ValueError:
                    num = get_slot_auto_number(cname, slot_key)
            else:
                num = get_slot_auto_number(cname, slot_key)
            
            coupon["latest"] = num
            results[cname] = num

        draw_id = f"D-{int(now_ts * 1000) % 1000000:06d}"
        latest_draw = {
            "id": draw_id,
            "time": time_str,
            "date": date_str,
            "timestamp": int(now_ts * 1000),
            "results": results,
            "type": draw_type
        }

        state["latest_draw"] = latest_draw
        state["history"].insert(0, latest_draw)
        # Cap history to 500 draws
        if len(state["history"]) > 500:
            state["history"] = state["history"][:500]

        # Overrides are consumed and cleared!
        state["overrides"] = {}
        # Schedule next draw boundary
        state["next_draw_ts"] = calc_next_draw_timestamp(now_ts)
        save_state()

        print(f"[DRAW] {draw_type.upper()} executed at {time_str}: {results}")
        return latest_draw


def auto_draw_worker():
    """Background worker that continuously evaluates the 15-minute lottery cycle."""
    while True:
        try:
            now_ts = time.time()
            with state_lock:
                target_ts = state.get("next_draw_ts", 0)
            
            if now_ts >= target_ts:
                execute_draw("auto")
            
            time.sleep(1)
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
            time.sleep(1)


class LotteryHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Enable CORS and disable aggressive browser caching for API & HTML
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Cache-Control')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, status_code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Route: API status
        if path == '/api/status':
            with state_lock:
                now_ts = time.time()
                remaining = max(0, int(state.get("next_draw_ts", now_ts) - now_ts))
                target_iso = datetime.fromtimestamp(state.get("next_draw_ts", now_ts), tz=timezone.utc).isoformat()
                
                resp = {
                    "ok": True,
                    "serverTime": format_time_12h(now_ts),
                    "serverTimestamp": int(now_ts * 1000),
                    "nextDrawTimestamp": int(state.get("next_draw_ts", now_ts) * 1000),
                    "nextDrawTime": format_time_12h(state.get("next_draw_ts", now_ts)),
                    "remainingSeconds": remaining,
                    "intervalMinutes": DRAW_INTERVAL_MINS,
                    "coupons": [dict(c) for c in state.get("coupons", DEFAULT_COUPONS)],
                    "overrides": dict(state.get("overrides", {})),
                    "latestDraw": state.get("latest_draw", {}),
                    "totalDrawsToday": len(state.get("history", [])),
                    "history": list(state.get("history", []))[:15]
                }
            self.send_json(resp)
            return

        # Route: API history
        if path == '/api/history':
            params = urllib.parse.parse_qs(parsed.query)
            hours = 12
            try:
                if 'hours' in params:
                    hours = int(params['hours'][0])
            except ValueError:
                hours = 12

            with state_lock:
                history_list = list(state.get("history", []))
                coupons_list = [dict(c) for c in state.get("coupons", DEFAULT_COUPONS)]
            
            # If history is empty, synthesize deterministic past draws so users always have records
            if len(history_list) == 0:
                history_list = self.generate_synthetic_history(hours, coupons_list)

            self.send_json({"ok": True, "hours": hours, "history": history_list[:hours * 4]})
            return

        # Friendly URL rewrites:
        # '/' -> 'index.html'
        # '/admin' -> 'admin.html'
        if path == '/' or path == '':
            self.path = '/index.html'
        elif path == '/admin':
            self.path = '/admin.html'

        return super().do_GET()

    def generate_synthetic_history(self, hours, coupons_list):
        now_ts = time.time()
        fifteen_min = 15 * 60
        slots = hours * 4
        records = []
        for i in range(1, slots + 1):
            slot_ts = now_ts - (i * fifteen_min)
            seed = int(slot_ts // fifteen_min)
            n = abs((seed * 7919 + 1013904223) % 100)
            c = abs((seed * 6271 + 314159265) % 100)
            r = abs((seed * 9319 + 271828183) % 100)
            records.append({
                "id": f"#D-{abs(seed) % 1000000:06d}",
                "time": format_time_12h(slot_ts),
                "date": format_date(slot_ts),
                "timestamp": int(slot_ts * 1000),
                "results": {"NAVRATNA": n, "CHETAK": c, "RAJA RANI": r},
                "type": "auto"
            })
        return records

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'
        try:
            req_data = json.loads(body) if body else {}
        except Exception:
            req_data = {}

        # Route: Save overrides for next draw
        if path == '/api/override':
            overrides = req_data.get("overrides", {})
            valid_ov = {}
            for k, v in overrides.items():
                if v is not None and str(v).strip() != "":
                    try:
                        n = int(v)
                        if 0 <= n <= 99:
                            valid_ov[str(k).strip()] = n
                    except (ValueError, TypeError):
                        pass

            with state_lock:
                state["overrides"] = valid_ov
                save_state()

            print(f"[OVERRIDE] Saved overrides: {valid_ov}")
            self.send_json({"ok": True, "message": "Overrides saved for next draw", "overrides": valid_ov})
            return

        # Route: Manual trigger disabled per specification
        if path == '/api/draw':
            self.send_json({"ok": False, "error": "Manual trigger disabled. All draws occur automatically every 15 minutes."}, 400)
            return

        # Route: Clear overrides
        if path == '/api/clear':
            with state_lock:
                state["overrides"] = {}
                save_state()
            print("[OVERRIDE] Cleared all overrides")
            self.send_json({"ok": True, "message": "Overrides cleared. Next draw will randomize."})
            return

        self.send_json({"ok": False, "error": "Endpoint not found"}, 404)


def run_server():
    load_or_init_state()

    # Start auto-draw background thread
    worker_thread = threading.Thread(target=auto_draw_worker, daemon=True)
    worker_thread.start()
    print("[INIT] 15-Minute auto-draw scheduler started.")

    class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        allow_reuse_address = True

    server_address = ('0.0.0.0', PORT)
    with ThreadedHTTPServer(server_address, LotteryHandler) as httpd:
        print(f"============================================================")
        print(f"  Golden Navratna Kuber Server is LIVE!")
        print(f"  Public Website: http://localhost:{PORT}")
        print(f"  Admin Panel:    http://localhost:{PORT}/admin.html")
        print(f"  REST API:       http://localhost:{PORT}/api/status")
        print(f"============================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[STOP] Server stopped by user.")


if __name__ == '__main__':
    run_server()
