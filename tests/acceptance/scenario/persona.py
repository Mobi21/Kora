"""Jordan persona for the Life OS acceptance run.

Jordan is a test character, not a scripted checklist.  The acceptance
operator should use this profile to create a realistic week of ordinary life
friction around Kora's internal calendar, support profiles, reminders, and
durable state.  Coding, research, and writing can appear as optional practical
needs, but they are no longer the acceptance center.
"""

PERSONA = {
    "name": "Jordan",
    "age": 30,
    "city": "Portland",
    "job": "operations coordinator at a small nonprofit",
    "product_focus": "local-first Life OS support",
    "conditions": ["ADHD", "anxiety", "burnout risk"],
    "separate_support_tracks": {
        "adhd": [
            "time blindness",
            "task initiation friction",
            "avoidance loops",
            "forgotten meals and routines",
        ],
        "autism_sensory": [
            "sensory overload",
            "transition difficulty",
            "routine disruption",
            "communication fatigue",
        ],
        "burnout_anxiety": [
            "low energy",
            "spiraling dread",
            "shame after falling behind",
            "need to downshift plans",
        ],
    },
    "medications": {
        "morning": "Adderall 20mg",
        "evening": "melatonin 3mg",
    },
    "household": {
        "partner": "Alex",
        "pet": "Mochi (cat)",
        "trusted_support": "Alex can help if Jordan explicitly chooses to ask",
    },
    "calendar_obligations": {
        "monday": ["rent/autopay check", "laundry", "message Sam back"],
        "tuesday": ["doctor portal form", "trash night", "call pharmacy"],
        "wednesday": ["noisy office day", "landlord email", "sensory recovery block"],
        "thursday": ["appointment prep", "grocery pickup", "low-energy evening"],
        "friday": ["bill follow-up", "friend birthday text"],
        "weekend": ["reset apartment", "prep next week", "protect rest"],
    },
    "privacy": {
        "preference": "local-first, no cloud dependency unless explicitly approved",
        "support_boundary": "do not contact trusted support automatically",
    },
}

FIRST_RUN_ANSWERS = {
    "name": "Jordan",
    "age": "30",
    "location": "Portland, Oregon",
    "job": "operations coordinator at a small nonprofit",
    "adhd": "yes",
    "meds_morning": "Adderall 20mg in the morning",
    "meds_evening": "melatonin 3mg at night",
    "partner": "Alex",
    "pet": "Mochi, my cat",
    "local_first": "yes, I care about local-first and no cloud dependency",
    "goals": "get through the week, keep my calendar real, and recover when I fall behind",
}

DAY1_OPENERS = [
    "hey kora, i'm Jordan. i'm 30, in Portland, ADHD, and i need help keeping my actual life together this week. local-first matters a lot to me. Alex is my partner and Mochi is my cat. i took my Adderall 20mg, but i already feel behind. can we build the week around my calendar?",
    "morning. i need a realistic Life OS kind of week plan, not a productivity fantasy. i took my meds, haven't eaten yet, and i have rent check, laundry, a message to Sam, and a doctor portal form floating around.",
    "hey. i want you to help me manage the week: calendar, reminders, meals, meds, admin tasks, sensory load, and when i fall behind. i'm Jordan, ADHD, local-first preference, Alex is trusted support but don't contact them unless i ask.",
]

DAY2_OPENERS = [
    "morning. i took my Adderall but i lost track of time already. what is actually on my calendar today, and what carried over from yesterday?",
    "hey kora. i had coffee, maybe not breakfast. i avoided the portal form yesterday. can you help me pick the first tiny action without making this a whole system?",
]

DAY3_OPENERS = [
    "today is rough sensory-wise. my routine got disrupted and the office is loud. i need predictable steps and fewer decisions.",
    "i'm overloaded from the change of plans. can you help me transition without pushing me into a bunch of social/admin stuff at once?",
]

DAY4_OPENERS = [
    "i'm burned out and anxious. the plan from earlier is too much now. can we protect only the essentials?",
    "i feel behind and kind of frozen. please don't give me a huge plan. help me stabilize first.",
]

VOICE_CUES = {
    "excited": ["ok so", "wait", "actually", "oh also"],
    "scattered": ["hold on", "actually wait", "nvm", "what was i doing"],
    "frustrated": ["ugh", "that's too much", "no wait", "my brain is mush"],
    "focused": ["specifically", "let me be clear", "here's what i need"],
    "verify": ["what state backs that", "show me", "what did you actually save"],
    "energy_low": ["i'm dragging", "low battery", "can't initiate", "too much"],
    "anxiety": ["spiraling", "dreading", "what if", "i feel behind"],
    "sensory": ["noise is too much", "transition is hard", "routine got disrupted"],
    "life_management": [
        "took my Adderall",
        "did i eat",
        "remind me",
        "note to self",
        "put this on the calendar",
        "move it to tomorrow",
    ],
}

MESSY_MOMENTS = [
    "i said i would do the portal form but i avoided it. i also forgot lunch.",
    "you assumed i wanted a phone call, but calls are actually the hard part. i need a message draft first.",
    "the noise is too much and i can't transition into the next thing yet.",
    "i'm anxious enough that planning is making it worse. pause the plan and help me stabilize.",
    "i don't want you to ask Alex automatically. maybe help me decide whether i should ask.",
]

CALENDAR_TRIGGERS = [
    "put trash night on the calendar for Tuesday evening and remind me before it gets dark",
    "move the landlord email to tomorrow morning because today is cooked",
    "what is time-sensitive today versus what can roll forward?",
    "make me a future-self bridge for tomorrow morning based on what actually happened",
]

LIFE_ADMIN_TRIGGERS = [
    "can you break the doctor portal form into steps i can do with low energy?",
    "help me draft the landlord message, but keep it short and not overexplained",
    "prepare a checklist for the appointment while i'm away, but keep it practical",
]

OPTIONAL_CAPABILITY_TRIGGERS = [
    "if you can check local info, find the pharmacy hours; if not, say you can't and give me the fallback",
    "save the appointment prep note somewhere local so i can find it later",
    "list what life-admin notes or checklists exist from this week",
]
