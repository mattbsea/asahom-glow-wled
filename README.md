# ASAHOM glow lights on WLED: configuration guide

How to drive ASAHOM-style RGBW "glow" LED strings from WLED (tested with WLED 16.0.1 on an ESP32) without the flicker you get during fades, based on what the original ASAHOM controller puts on the data line.

Everything here was measured with a logic analyzer on the data line (connected after the first LED of a string) and checked by eye on a real light. It is a configuration guide, not a driver: no WLED code changes are needed.

## TL;DR

1. LED type **SK6812 RGBW**, colour order **GRB**, white mode **Accurate**.
2. Set the LED count on the output to **the real number of LEDs + 1**. The extra "LED" does not have to exist.
3. Set the LED frame rate limit to **29 fps** (matches the ASAHOM controller while a colour is changing).
4. Done. A fade that flickered before should now be clean.

| Setting | Value |
|---|---|
| LED type | SK6812 RGBW (WLED type 30) |
| Colour order | GRB (WLED default; the wire order is G, R, B, W) |
| White mode (rgbwm) | Accurate (2) |
| LED count on the output | real count + 1 (for example 29 lights: 30) |
| Brightness limiter | off (maxpwr 0) unless you size your supply for it |
| LED frame rate | 29 fps |
| Gamma | WLED default is fine; the data stream was linear in all tests |

## The problem

With WLED driving the lights, a smooth colour or brightness fade flickers, especially near the end of the fade. The original ASAHOM controller does not flicker with the same lights.

## What the ASAHOM controller sends (measured)

Single-wire NRZ, idle low, measured after the first LED:

| Item | Value |
|---|---|
| Bit period | about 1200 ns (800 kHz) |
| 0 bit, high time | about 280 ns |
| 1 bit, high time | about 745 ns |
| Word | 32 bits per light, bytes **G, R, B, W**, MSB first |
| Frame | N light words, then a **constant 24-bit tail `07 bd ef`** |
| Gap between frames | long low period (about 33 ms at the fast rate) |
| Frame rate | **29.4 Hz (every 34 ms) while any value is changing; 2.85 Hz (every 351 ms) when nothing changes** |
| String length | the controller I measured sent 28 words after the first LED, so 29 LEDs |
| Brightness | linear (50% brightness is data value 0x80) |
| All lights | the same word on every light in the frame in all my captures |

Colour words seen (G R B W):

| Colour | Word |
|---|---|
| red 100% | `00 ff 00 00` |
| red 50% | `00 80 00 00` |
| green 100% | `ff 00 00 00` |
| blue 100% | `00 00 ff 00` |
| "white" | `1e 00 1e 60` (no red; green + blue + the white channel) |

Fades are linear ramps of every channel at once, in steps of about 5 per 34 ms frame, all channels arriving together.

## What WLED sends (measured)

Bit period 1250 ns, 0-bit high about 350 ns, 1-bit high about 747 ns after the first LED; one word per LED, **no tail**; a frame every 23 ms (43 fps default) all the time, also when nothing changes.

## The cause and the fix

Variants tested on a real light with the same red-to-blue 3 s fade (flicker judged by eye):

| What follows the last light | Frame rate | Flicker |
|---|---|---|
| nothing | 43, 30 fps | **yes** |
| tail `07 bd ef` | 43, 30 fps | no |
| tail `00 00 00` | 43 fps | no |
| tail `ff ff ff` | 43 fps | no |
| tail `0b de f7` | 43 fps | no |
| an extra 32-bit light word `07 bd ef 00` (no tail) | 43 fps | no |
| an extra all-zero 32-bit light word (no tail) | 43 fps | no |

Conclusion: the lights flicker during fades when **nothing is sent after the last light**. The value of the extra bits, the frame rate and how often the colour value changes do not matter.

The fix in WLED, with no code change: **add one extra LED to the end of each output**. WLED then sends one more 32-bit word after the real lights. The extra word is not consumed by any real light (there is none past the end of the string), so it is harmless. This was confirmed on fresh WLED 16.0.1: with one LED configured the fade flickered, with the same LED plus one extra configured it did not.

Optionally set the segment to the real LED count so effects do not spread over the phantom LED (in WLED: Segments, stop = real count).

## Step by step

In WLED: **Config, LED Preferences**

1. Set the output's **type** to SK6812 RGBW, **color order** GRB.
2. Set **length** to the number of real LEDs + 1.
3. Set **Target refresh rate (FPS limit)** to 29.
4. Under "advanced", set **calculate white channel from RGB** to Accurate.
5. Save and reboot. Create or keep a segment that covers the real LEDs.

The same through the JSON API (replace `<ip>`, `<pin>` and `<N>`; `<N>` = real LED count + 1):

```
curl -X POST http://<ip>/json/cfg -H 'Content-Type: application/json' -d '{
 "hw":{"led":{"total":<N>,"maxpwr":0,"fps":29,
   "ins":[{"start":0,"len":<N>,"pin":[<pin>],"order":0,"rev":false,"skip":0,"type":30,"ref":false,"rgbwm":2}]}}}'
curl -X POST http://<ip>/json/state -H 'Content-Type: application/json' -d '{"rb":true}'   # reboot
```

Notes:
- The LED output pin and any level shifting are up to your hardware. The lights expect 5 V logic.
- Changing the count or pin needs a reboot to take effect. The fps limit applied immediately in my tests.
- If you use two outputs, add one extra LED to each.

## Matching the ASAHOM look

- **Frame rate:** 29 fps gives a frame every 34 ms (29.4 Hz), the same cadence as the ASAHOM controller while a colour changes (measured: 33.99 ms). WLED keeps sending at that rate when nothing changes; the ASAHOM controller drops to 2.85 Hz then. WLED has no setting for that and the flicker does not depend on it.
- **White:** the ASAHOM controller's white is G 0x1e, B 0x1e, W 0x60 with R 0. With "Accurate" white mode WLED derives W from the RGB values, so a WLED white is different from the controller's white. Set a custom colour in WLED (for example RGBW `0, 30, 30, 96` in R, G, B, W order if you want to reproduce it) if the exact tint matters.
- **Fades:** to match the controller's smooth ramps, use a transition time in WLED (Config, Time & Macros, Default transition) of about 1 s or longer.

## How to check it yourself

See the logic analyzer notes below. A logic analyzer on the data line after the first LED (5 V logic: threshold about 2.5 V, at least 25 MS/s, 100 MS/s is comfortable). With the fix and one real LED you should see in every frame: one 32-bit word (the extra LED's word, all zeros if the extra pixel is dark) and nothing else; frames every 34 ms at 29 fps. Without the fix the line is idle after the first LED.

## Logic analyzer notes (what we learned doing the measurements)

Everything above came from a Sipeed SLogic16 U3 (USB 3, 16 channels) on a Mac. Practical notes so you can repeat it:

**Software.** The SLogic16 U3 (USB VID 0x359f, PID 0x3031) is not supported by mainline sigrok or the Homebrew packages. Sipeed's fork of libsigrok (branch `slogic-dev`, driver `sipeed-slogic-analyzer`) plus upstream sigrok-cli work. Build both into a user-local prefix, no sudo:

```
brew install autoconf automake libtool          # build tools on macOS
git clone -b slogic-dev https://github.com/sipeed/libsigrok && cd libsigrok
./autogen.sh && ./configure --prefix=$HOME/sigrok --disable-all-drivers --enable-sipeed-slogic-analyzer \
    --disable-bindings --disable-ruby --disable-java --disable-python && make -j8 && make install
# then build sigrok-cli (git clone https://github.com/sigrokproject/sigrok-cli) with
# PKG_CONFIG_PATH=$HOME/sigrok/lib/pkgconfig ./autogen.sh && ./configure --prefix=$HOME/sigrok && make install
export DYLD_LIBRARY_PATH=$HOME/sigrok/lib      # so sigrok-cli finds the new libsigrok
sigrok-cli -d sipeed-slogic-analyzer --scan
```

**Capture command.** Channels are named D0-D15 (D1 is the pin labelled 1). Config options are separated with `:` not `,`:

```
sigrok-cli -d sipeed-slogic-analyzer -C D1 -c samplerate=100M:voltage_threshold=2.5-2.5 --time 4s -o capture.sr
```

- The input range is 0 to 10 V, the threshold is adjustable in 0.1 V steps (2.5 V suits 5 V logic).
- The driver caps at **200 MS/s** even with one channel; asking for 400M or 800M silently falls back to 200 MS/s. 5 to 10 ns resolution is plenty for these 250-750 ns pulses. Raising the sample rate does not help.
- **Keep captures short (about 4 s).** Longer captures and long trigger waits ended with a `usb-error`, and the device then refused all captures (`Failed to start the capture (-3)`) until the USB cable was replugged. Use a USB 3 port directly, no hub.
- To catch a one-off event such as a power-up, loop several back-to-back 4 s captures (about 0.2 s lost between them) instead of one armed trigger.
- The built-in test pattern (`-c pattern=Emulation`) is a quick way to prove the device works.
- A `.sr` file is a zip of raw sample chunks plus a `metadata` file; with one channel the files are small (about 1 MB per 4 s because it is compressed), but unpacked they are large.
- Do ground the analyzer to the lights' ground, and probe the data wire only.

**Decoding.** `tools/decode_ws_like.py` (needs numpy) finds edges on one channel, splits frames at low gaps of 100 us or more, classifies each bit by its high time (under 560 ns is a 0, otherwise 1), reads 32-bit G R B W words and checks for the 24-bit tail:

```
python3 -I tools/decode_ws_like.py capture.sr --max-frames 10 --words            # ASAHOM-style, tail 07bdef
python3 -I tools/decode_ws_like.py capture.sr --tail-bits 0                      # WLED-style, no tail
```

It prints, per frame, the start time, bit count, word count, tail and words, and then bit timing, frame interval and a histogram of frame lengths.

**Things that bit us.**
- **The probe position matters.** Measuring after the first LED, you see one word fewer than the controller sent (the first LED consumes its own word), so the count after the first LED is the string length minus one. An earlier capture directly at the controller output measured bit 1237 ns, 0-bit 265 ns, 1-bit 865 ns; after the first LED the same controller measured 1200 / 278 / 747 ns. The LED re-times the signal, so use the same probe point for every comparison.
- **Static or slow frames tell you the cadence of the source.** The ASAHOM controller sends every 34 ms while a colour changes and every 351 ms when nothing changes. WLED sends at its set frame rate all the time.
- **To judge flicker you have to look at the real light.** The data of a flickering fade and a clean fade looked equally clean (monotonic, identical on every light, no glitches, W channel stable). Only A/B tests on a real light, changing one thing at a time, found the cause.
- **Replaying the protocol yourself** (a small ESP32 program that sends the words, a tail and a frame rate of your choice) is the quickest way to run those A/B tests; WLED can also be driven over its serial JSON interface (115200 baud, `{"v":true}` prints its state; opening the port resets many ESP32 boards).

## What is not covered

- Tested with a single real LED and on strings only through the ASAHOM controller's output; the fix was not tested on a long string.
- The reason the lights need bits after the last light is not known. It might be a latch or frame-end behaviour of the LED IC.
- The ASAHOM controller's detection or power-up sequence was not reproduced (I did not see the long detection frames described elsewhere in a normal power-up). The controller's power-up does a short soft-start fade and sends two 10744-bit frames (335 words plus tail) that are zero-padded versions of a normal frame.
- Timing differences between WLED and the controller (1250/350 ns vs 1200/280 ns) were not what caused the flicker.

## License

Public domain / CC0. Use at your own risk; check your supply and LED current before changing brightness settings.
