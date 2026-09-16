# Loyola Networking — Android app

The phone client for the campus network. It talks to `/api/v1` on the FastAPI
server in the parent directory and to nothing else.

It exists because verification is a camera problem. The whole network is gated
on photographing a college ID card, and that is the one thing a phone does
better than a laptop. Everything else the app does, the web app also does.

## Running it

```bash
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8011       # emulator
flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8011   # real phone
```

`10.0.2.2` is the Android emulator's alias for the host machine's localhost. On
a physical device, use the laptop's LAN address and make sure uvicorn is bound
to `0.0.0.0`, not `127.0.0.1`.

The compiled address is only a default — the sign-in screen has a server field,
because during a rollout the address changes more often than the app does.

## Building

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://loyola.avlokai.com
flutter build apk --release --split-per-abi --dart-define=API_BASE_URL=...
```

`--split-per-abi` produces one APK per architecture — about a third the size of
the universal one, which matters when students are installing it over campus
wifi or mobile data.

Release builds are still signed with the debug key. Fine for sideloading during
a pilot; set a real signing config in `android/app/build.gradle.kts` before the
college distributes it.

## Checks

```bash
flutter analyze
flutter test
```

The tests cover the formatters, the deliberately forgiving JSON parsing (an
older build in a student's pocket must not crash when the server adds or drops
a field) and the tier gates that decide who may read and who may write.

## How it is put together

```
lib/
  core/
    config.dart       where the server is; the only place that knows
    api_client.dart   HTTP, bearer auth, and turning failures into one exception
    session.dart      who is signed in — the single source of truth
    theme.dart        the palette and component styling
    format.dart       relative times, compact counts, initials
  data/repository.dart every call the app can make, in one class
  models/models.dart   plain data classes mirroring the API's JSON
  screens/             one file per screen
  widgets/             post card, vote bar, avatars, empty and error states
```

Three deliberate choices:

**Routing is on state, not history.** `main.dart` renders sign-in, the
verification wall or the app shell based on `Session.stage`. The wall cannot be
skipped with a back gesture or a stale route.

**Votes are optimistic, the server is authoritative.** The tap updates
immediately, then the number the server returns replaces it — the server applies
reputation weighting and anti-gaming rules the client cannot predict, so its
answer always wins, and a rejection rolls the UI back.

**Notifications are polled, not pushed.** Push would mean a Firebase project for
the college to administer, and a campus network does not need to buzz in
anyone's pocket during a lecture.

## Icons

`tool/make_launcher_icons.py` renders the launcher icon from the same monogram
the app draws on its splash screen. The output is committed, so a normal build
needs neither Python nor Pillow; re-run it only if the brand colour changes.
