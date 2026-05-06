import micropython
import time
from stellar import StellarUnicorn, Channel
from picographics import PicoGraphics, DISPLAY_STELLAR_UNICORN
from digits import digits, DIGIT_BYTES

su = StellarUnicorn()
graphics = PicoGraphics(display=DISPLAY_STELLAR_UNICORN)

WORK_SECONDS = 25 * 60
BREAK_SECONDS =  5 * 60
NUM_CYCLES = 4
DEBOUNCE_MS = 250
DOUBLE_PRESS_MS = 2000
POLL_MS = 50
POLL_MS_IDLE = 100
COUNTDOWN_BLINK_FROM = 10

# Brightness as integer 1..10, avoids float drift across repeated steps.
BRIGHTNESS_DIV = 10
BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 10
BRIGHTNESS_STEP = 1

PEN_OFF = graphics.create_pen(0, 0, 0)
PEN_WORK = graphics.create_pen(0, 80, 220)
PEN_BREAK = graphics.create_pen(220, 80, 0)
PEN_DONE = graphics.create_pen(0, 255, 0)

IDLE = 0
RUNNING = 1
PAUSED = 2
DONE = 3

S_STATE = 0
S_CYCLE = 1
S_PHASE = 2
S_REMAIN = 3

BEEP_IDLE = 0
BEEP_PLAYING = 1
BEEP_RELEASING = 2
BEEP_GAP_MS = 150

audio_ch = su.synth_channel(0)
audio_ch.configure(
    waveforms=Channel.SINE,
    volume=0.5,
    attack=0.01,
    decay=0.05,
    sustain=0.8,
    release=0.1,
)

# [digit_idx_pos0..3, last_fg_pen] — invalidated with -1.
_render_cache = [-1, -1, -1, -1, -1]

# [phase, event_deadline_ms]
_audio_state = [BEEP_IDLE, 0]
_beep_queue = []


def render_invalidate():
    _render_cache[0] = -1
    _render_cache[1] = -1
    _render_cache[2] = -1
    _render_cache[3] = -1
    _render_cache[4] = -1


@micropython.native
def draw_digit(digit_idx, col_offset, row_offset, fg_pen, bg_pen):
    base = digit_idx * DIGIT_BYTES
    glyph = digits
    graphics.set_pen(bg_pen)
    graphics.rectangle(col_offset, row_offset, 8, 8)
    graphics.set_pen(fg_pen)
    for row in range(8):
        bits = glyph[base + row]
        if bits == 0:
            continue
        y = row_offset + row
        col = col_offset
        m = 0x80
        while m:
            if bits & m:
                graphics.pixel(col, y)
            col += 1
            m >>= 1


@micropython.native
def render_time(total_seconds, on_pen):
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    cache = _render_cache
    new0 = minutes // 10
    new1 = minutes % 10
    new2 = seconds // 10
    new3 = seconds % 10
    pen_changed = on_pen != cache[4]
    dirty = False
    if pen_changed or new0 != cache[0]:
        draw_digit(new0, 0, 0, on_pen, PEN_OFF)
        cache[0] = new0
        dirty = True
    if pen_changed or new1 != cache[1]:
        draw_digit(new1, 8, 0, on_pen, PEN_OFF)
        cache[1] = new1
        dirty = True
    if pen_changed or new2 != cache[2]:
        draw_digit(new2, 0, 8, on_pen, PEN_OFF)
        cache[2] = new2
        dirty = True
    if pen_changed or new3 != cache[3]:
        draw_digit(new3, 8, 8, on_pen, PEN_OFF)
        cache[3] = new3
        dirty = True
    if dirty:
        cache[4] = on_pen
        su.update(graphics)


def render_done():
    render_invalidate()
    graphics.set_pen(PEN_DONE)
    graphics.clear()
    su.update(graphics)


def render_blank():
    render_invalidate()
    graphics.set_pen(PEN_OFF)
    graphics.clear()
    su.update(graphics)


def beep_enqueue(freq, dur_ms=300):
    _beep_queue.append((freq, dur_ms))


def service_audio(now):
    st = _audio_state
    phase = st[0]
    if phase == BEEP_PLAYING:
        if time.ticks_diff(now, st[1]) >= 0:
            audio_ch.trigger_release()
            st[0] = BEEP_RELEASING
            st[1] = time.ticks_add(now, BEEP_GAP_MS)
            phase = BEEP_RELEASING
    if phase == BEEP_RELEASING:
        if time.ticks_diff(now, st[1]) >= 0:
            su.stop_playing()
            st[0] = BEEP_IDLE
            phase = BEEP_IDLE
    if phase == BEEP_IDLE and _beep_queue:
        freq, dur = _beep_queue.pop(0)
        audio_ch.frequency(freq)
        su.play_synth()
        audio_ch.trigger_attack()
        st[0] = BEEP_PLAYING
        st[1] = time.ticks_add(now, dur)


def audio_busy():
    return _audio_state[0] != BEEP_IDLE or len(_beep_queue) > 0


def phase_pen(phase):
    return PEN_WORK if phase == 0 else PEN_BREAK


def apply_brightness(bri_int):
    su.set_brightness(bri_int / BRIGHTNESS_DIV)


def do_reset(sv):
    sv[S_STATE] = IDLE
    sv[S_CYCLE] = 0
    sv[S_PHASE] = 0
    sv[S_REMAIN] = WORK_SECONDS
    render_invalidate()
    render_time(WORK_SECONDS, PEN_WORK)


def run():
    sv = [IDLE, 0, 0, WORK_SECONDS]
    deadline = 0
    last_display_s = -1

    user_bri = int(round(su.get_brightness() * BRIGHTNESS_DIV))
    if user_bri < BRIGHTNESS_MIN:
        user_bri = BRIGHTNESS_MIN
    elif user_bri > BRIGHTNESS_MAX:
        user_bri = BRIGHTNESS_MAX
    apply_brightness(user_bri)
    display_on = True
    blink_is_bright = False  # current state of the countdown blink brightness

    a_down = False
    b_down = False
    c_down = False
    bup_down = False
    bdn_down = False
    last_a_ms = 0
    last_b_ms = 0
    last_c_ms = 0
    last_bup_ms = 0
    last_bdn_ms = 0
    first_b_ms = 0

    render_time(WORK_SECONDS, PEN_WORK)
    last_display_s = WORK_SECONDS

    while True:
        now = time.ticks_ms()
        service_audio(now)

        a_cur = su.is_pressed(StellarUnicorn.SWITCH_A)
        a_pressed = (a_cur and not a_down
                     and time.ticks_diff(now, last_a_ms) > DEBOUNCE_MS)
        if a_pressed:
            last_a_ms = now
        a_down = a_cur

        b_cur = su.is_pressed(StellarUnicorn.SWITCH_B)
        b_pressed = (b_cur and not b_down
                     and time.ticks_diff(now, last_b_ms) > DEBOUNCE_MS)
        if b_pressed:
            last_b_ms = now
        b_down = b_cur

        bup_cur = su.is_pressed(StellarUnicorn.SWITCH_BRIGHTNESS_UP)
        if bup_cur and not bup_down and time.ticks_diff(now, last_bup_ms) > DEBOUNCE_MS:
            if user_bri < BRIGHTNESS_MAX:
                user_bri += BRIGHTNESS_STEP
                if display_on and not blink_is_bright:
                    apply_brightness(user_bri)
            last_bup_ms = now
        bup_down = bup_cur

        bdn_cur = su.is_pressed(StellarUnicorn.SWITCH_BRIGHTNESS_DOWN)
        if bdn_cur and not bdn_down and time.ticks_diff(now, last_bdn_ms) > DEBOUNCE_MS:
            if user_bri > BRIGHTNESS_MIN:
                user_bri -= BRIGHTNESS_STEP
                if display_on and not blink_is_bright:
                    apply_brightness(user_bri)
            last_bdn_ms = now
        bdn_down = bdn_cur

        c_cur = su.is_pressed(StellarUnicorn.SWITCH_C)
        if c_cur and not c_down and time.ticks_diff(now, last_c_ms) > DEBOUNCE_MS:
            display_on = not display_on
            if display_on:
                apply_brightness(user_bri)
                blink_is_bright = False
                if sv[S_STATE] == DONE:
                    render_done()
                else:
                    render_invalidate()
                    render_time(sv[S_REMAIN], phase_pen(sv[S_PHASE]))
                last_display_s = sv[S_REMAIN]
            else:
                su.set_brightness(0.0)
                blink_is_bright = False
                render_blank()
            last_c_ms = now
        c_down = c_cur

        reset_now = False
        if b_pressed:
            if first_b_ms != 0 and time.ticks_diff(now, first_b_ms) <= DOUBLE_PRESS_MS:
                reset_now = True
                first_b_ms = 0
            else:
                first_b_ms = now
        elif first_b_ms != 0 and time.ticks_diff(now, first_b_ms) > DOUBLE_PRESS_MS:
            first_b_ms = 0

        if reset_now:
            if blink_is_bright and display_on:
                apply_brightness(user_bri)
                blink_is_bright = False
            do_reset(sv)
            last_display_s = sv[S_REMAIN]
            time.sleep_ms(POLL_MS)
            continue

        if a_pressed:
            state = sv[S_STATE]
            if state == IDLE:
                sv[S_STATE] = RUNNING
                deadline = time.ticks_add(now, sv[S_REMAIN] * 1000)
            elif state == RUNNING:
                sv[S_STATE] = PAUSED
                sv[S_REMAIN] = max(0, time.ticks_diff(deadline, now) // 1000)
            elif state == PAUSED:
                sv[S_STATE] = RUNNING
                deadline = time.ticks_add(now, sv[S_REMAIN] * 1000)
            elif state == DONE:
                do_reset(sv)
                last_display_s = sv[S_REMAIN]

        if sv[S_STATE] == RUNNING:
            ticks_left = time.ticks_diff(deadline, now)
            sv[S_REMAIN] = max(0, ticks_left // 1000)

            if sv[S_REMAIN] != last_display_s:
                if display_on and sv[S_REMAIN] <= COUNTDOWN_BLINK_FROM:
                    want_bright = (sv[S_REMAIN] % 2 == 0)
                    if want_bright != blink_is_bright:
                        apply_brightness(BRIGHTNESS_MAX if want_bright else user_bri)
                        blink_is_bright = want_bright
                if display_on:
                    render_time(sv[S_REMAIN], phase_pen(sv[S_PHASE]))
                last_display_s = sv[S_REMAIN]

            if ticks_left <= 0:
                if blink_is_bright:
                    apply_brightness(user_bri)
                    blink_is_bright = False
                if sv[S_PHASE] == 0:
                    beep_enqueue(880)
                    sv[S_PHASE] = 1
                    sv[S_REMAIN] = BREAK_SECONDS
                else:
                    beep_enqueue(440)
                    sv[S_CYCLE] += 1
                    sv[S_PHASE] = 0
                    sv[S_REMAIN] = WORK_SECONDS

                if sv[S_CYCLE] >= NUM_CYCLES:
                    sv[S_STATE] = DONE
                    if display_on:
                        render_done()
                    beep_enqueue(660, 200)
                    beep_enqueue(880, 200)
                else:
                    deadline = time.ticks_add(time.ticks_ms(), sv[S_REMAIN] * 1000)
                last_display_s = -1

        if sv[S_STATE] == RUNNING or audio_busy():
            time.sleep_ms(POLL_MS)
        else:
            time.sleep_ms(POLL_MS_IDLE)


run()
