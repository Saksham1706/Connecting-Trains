# 🚆 Find Connecting Trains

**Find the best split-ticket routes when direct trains are full.** This application helps travelers find connecting train routes between any two stations in India, optimizing for layover time and departure preferences.

---

## 🚀 Live Demo
[Check out the live app here!](https://connecting-trains.streamlit.app/) 

---

## ✨ Features
- **Smart Connection Engine:** Automatically finds intersecting stations between two points.
- **Customizable Layovers:** Set your own maximum waiting time at the mid-station.
- **Time Window Filters:** Filter results based on when you want to leave or arrive.
- **Autocomplete Search:** Search thousands of stations by Name or Station Code.
- **Mobile Friendly:** Built with a responsive Streamlit UI.

---

## 🛠️ Technical Stack
- **Language:** Python 3.9+
- **Frontend/UI:** Streamlit
- **Data Processing:** Pandas
- **Logic:** Custom-built leg-matching algorithm with absolute time-delta calculations.

---

## 📌 Important Notes & Disclaimers
* **Data Currency:** The schedule data used in this project is dated up to **2023**.
* **Daily Running Assumption:** The current algorithm assumes every train runs every day. Users are advised to cross-verify specific running days on the official IRCTC website.
* **WIP:** This application is currently undergoing upgrades to include real-time calendar availability.

---

## 💻 Local Setup
1. **Clone the repo:**
   ```bash
   git clone [https://github.com/Saksham1706/Connecting-Trains.git](https://github.com/Saksham1706/Connecting-Trains.git)

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -r requirements.txt

streamlit run app.py



If you want to build something helpful & crazy, mail me at: wazirnoob@gmail.com
