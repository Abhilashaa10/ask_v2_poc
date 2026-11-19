"""Fake creator data for the POC (stands in for MySQL / Weaviate / saved nightly results)."""

AS_OF = "2026-11-18"

# Newest first. "latest" = VIDEOS[0], "1".."10" = position.
VIDEOS = [
    {
        "title": "I Tried Living on ₹5000 for a Month", "published": "2026-11-14", "length": "8:40",
        "views": 18200, "views_median": 31000, "ctr": 6.1, "ctr_median": 5.8,
        "retention": 31, "retention_median": 44, "avd": "2:41", "avd_median": "3:52",
        # % of viewers still watching at 0%, 10%, ... 100% of the video
        "curve": [100, 58, 47, 43, 40, 37, 35, 32, 29, 26, 21],
        "curve_median": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "intro_seconds": 52,
        "intro_text": "[0:00] Hey guys welcome back... [0:12] before we start, smash subscribe... "
                      "[0:25] quick story about my college canteen... [0:52] okay so the challenge is ₹5000.",
        "topics": ["budget challenge", "groceries", "rent split", "final savings"],
        "comments": [
            ("Skip to 0:52, intro is too long", 412, "intro too long"),
            ("Bro just tell us the result first", 233, "intro too long"),
            ("Loved the grocery part, very practical", 188, "praise"),
            ("Is ₹5000 even possible in Bangalore?", 97, "question"),
            ("Audio is low in the first minute", 64, "audio"),
        ],
        "doctor": "Title and thumbnail worked (CTR above median). Viewers left in the first 30 s: "
                  "52 s intro before the challenge starts.",
    },
    {
        "title": "5 Apps That Save Me Money Every Week", "published": "2026-11-07", "length": "10:12",
        "views": 41200, "views_median": 31000, "ctr": 7.4, "ctr_median": 5.8,
        "retention": 47, "retention_median": 44, "avd": "4:48", "avd_median": "3:52",
        "curve": [100, 79, 68, 61, 56, 52, 49, 46, 43, 39, 33],
        "curve_median": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "intro_seconds": 9,
        "intro_text": "[0:00] These 5 apps saved me ₹2,300 last month. App number one... ",
        "topics": ["app list", "cashback", "UPI tricks"],
        "comments": [
            ("App 3 is a game changer", 301, "praise"),
            ("Please make one for investing apps", 156, "request"),
        ],
        "doctor": "Strong hook in first 9 s, showed the result upfront. Best retention this month.",
    },
    {
        "title": "SIP vs FD: What Students Should Pick", "published": "2026-10-31", "length": "12:05",
        "views": 29800, "views_median": 31000, "ctr": 5.2, "ctr_median": 5.8,
        "retention": 42, "retention_median": 44, "avd": "5:04", "avd_median": "3:52",
        "curve": [100, 72, 60, 54, 50, 46, 43, 40, 37, 33, 28],
        "curve_median": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "intro_seconds": 20,
        "intro_text": "[0:00] SIP or FD? I put ₹1000 in both for a year...",
        "topics": ["SIP basics", "FD rates", "risk"],
        "comments": [
            ("FD rates shown are old, they changed in October", 211, "wrong info"),
            ("Too much jargon in the middle part", 144, "confusing"),
            ("Finally someone explained XIRR simply", 120, "praise"),
            ("Graph at 6:30 is hard to read on mobile", 58, "visuals"),
        ],
        "doctor": "Average. Comments flag outdated FD rates; middle section too technical.",
    },
    {
        "title": "My First Salary: How I Split It", "published": "2026-10-24", "length": "7:30",
        "views": 36500, "views_median": 31000, "ctr": 6.3, "ctr_median": 5.8,
        "retention": 46, "retention_median": 44, "avd": "3:27", "avd_median": "3:52",
        "curve": [100, 76, 65, 59, 54, 50, 47, 44, 41, 37, 31],
        "curve_median": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "intro_seconds": 12, "intro_text": "[0:00] My first salary was ₹32,000. Here's every rupee...",
        "topics": ["50/30/20", "emergency fund"], "comments": [("Relatable!", 140, "praise")],
        "doctor": "Good personal hook, above-median views.",
    },
    {
        "title": "Credit Card Mistakes I Made at 19", "published": "2026-10-17", "length": "9:15",
        "views": 33900, "views_median": 31000, "ctr": 5.9, "ctr_median": 5.8,
        "retention": 44, "retention_median": 44, "avd": "4:04", "avd_median": "3:52",
        "curve": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "curve_median": [100, 74, 63, 57, 52, 48, 45, 42, 39, 35, 30],
        "intro_seconds": 15, "intro_text": "[0:00] I owed ₹18,000 at 19. Here's how...",
        "topics": ["minimum due trap", "credit score"], "comments": [("Same happened to me", 90, "praise")],
        "doctor": "On median.",
    },
]

CHANNEL = {
    "subs": 84200, "subs_change_28d": "+1,240",
    "views_28d": 159600, "views_prev_28d": 194300,
    "retention_28d": 42, "retention_prev_28d": 45,
    "ctr_28d": 6.2, "ctr_prev_28d": 5.9,
    "watch_time_28d_h": 9850, "watch_time_prev_28d_h": 11920,
}

RIVALS = {
    "Finance With Riya": {"subs": 212000, "views_28d": 640000, "uploads_28d": 8,
                          "top": ["Zero-Budget Week Challenge (210K)", "Rent vs Buy at 25 (130K)"]},
    "Paisa Talks": {"subs": 97000, "views_28d": 188000, "uploads_28d": 4,
                    "top": ["I Tracked Every Rupee for 30 Days (71K)", "Best UPI Cashback Cards (44K)"]},
}

EXPERT_TIPS = [
    ("hook intro retention", "Start with the payoff or the result in the first 5-10 seconds; move channel asks later.", "YouTube Creator Academy"),
    ("intro subscribe", "Asking to subscribe before giving value raises early drop-off; ask after the first win.", "Paddy Galloway"),
    ("retention pacing", "Cut a pattern change every 20-40 seconds in talking-head videos.", "Retention Rabbit"),
    ("thumbnail ctr", "One face, one emotion, 3 words max on the thumbnail.", "VidIQ research"),
    ("posting time", "Post 2-3 hours before your audience peak so the video is indexed.", "YouTube Help"),
    ("comments engagement", "Pin a question comment in the first hour to seed replies.", "Think Media"),
]

TRENDS_SAVED = [
    ("No-spend challenges", "Challenge formats up 40% in personal-finance niche this month"),
    ("UPI credit lines", "New RBI rule news driving search interest"),
    ("Hostel budget meal prep", "Student audience searching budget food"),
]

VOICE = "Casual Hinglish, first-person stories, quick jokes, talks fast, uses ₹ examples."
NICHE = "Personal finance for Indian college students and first-jobbers (18-24)."
GROWTH_PLAN = ["Fix intros: result in first 10 s (your best video did this).",
               "Make a series from the challenge format (rival 'Zero-Budget Week' hit 210K).",
               "Reply to top question comments within 24 h."]
IDEAS = [("Investing Apps for Students: Tested with ₹500", "Top request in comments (156 likes)"),
         ("No-Spend Week in a Hostel", "Challenge format trending + rival success"),
         ("FD Rates Changed: Updated SIP vs FD", "Fixes outdated info flagged in comments")]

CREATOR = {
    "name": "@demo_channel",
    "platform": "youtube",
    "niche": "personal finance for students",
    "recent_videos": [v["title"] for v in VIDEOS],
    "rivals": list(RIVALS),
}
