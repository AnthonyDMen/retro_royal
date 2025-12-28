# Retro Royal

Retro Royal is a simple multiplayer arena game built in Python using **Pygame**. This repository contains the game client, headless server, and basic web-template for a test control hub.

---

## 📦 Requirements

This project uses:

- **Python 3.10.12**
- **Pygame >= 2.6.1**

Dependencies are listed in `requirements.txt`.

---

## Running in Dev

To run the game from source:

**1) Create and activate a virtual environment** (recommended):

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
```

**2) Install dependencies:**

```bash
pip install -r requirements.txt
```

**3) Start the game:**

```bash
python boot.py
```

---

## 🕹 Multiplayer

Retro Royal supports:

- Peer-to-peer hosting (one player hosts, others connect)
- Dedicated headless server (`headless_server.py`)
- Basic web control hub

*(This web interface runs locally — placeholder only.)*

---

## 🌐 Front-End / Web Control (placeholder)

A simple web front end supports control of headless servers and basic status monitoring.

**🔗 Front End URL (placeholder):**
https://front-end-placeholder.url

---

## 🧾 License

Retro Royal is released under the MIT License.

---

## 📸 Screenshots & Media

Add relevant screenshots and video here to show gameplay.

---

## 🤝 Contributing

Just the start, a foundation to an idea where anything can change.

---

## 💬 A Note

I’m not a programmer — I’m really just a guy with an idea and way too many hobbies. With my limited knowledge of Python, networking, free time, and many frustrating hours of learning and debugging, I somehow managed to put this together.

I used a couple of different ML models to help write, debug, clean, and format files to try and “organize” things where I could. I also wrote some of the minigames as learning projects over the years and repurposed mechanics, code, and ideas for this game.

My hope with Retro Royal is to present an open-source party game proof-of-concept that can grow into a well-written framework that others can easily contribute to — whether that’s a single spritesheet or a fully modded build. Ideally, this is just the start of a broader idea I’ve had.
