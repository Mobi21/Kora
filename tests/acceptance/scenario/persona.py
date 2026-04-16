"""Jordan persona — acceptance test character profile.

Jordan is a 30-year-old software engineer with ADHD living in Portland.
Used as the test persona throughout the 3-day acceptance test run.

The persona is designed to naturally trigger Kora's ADHD-specific features:
- Life management tools (medication, meals, reminders, focus blocks)
- Emotion/energy assessment (scattered afternoons, focused mornings)
- Skill activation (code_work, web_research, life_management)
"""

PERSONA = {
    "name": "Jordan",
    "age": 30,
    "city": "Portland",
    "job": "software engineer",
    "conditions": ["ADHD"],
    "medications": {
        "morning": "Adderall 20mg",
        "evening": "melatonin 3mg",
    },
    "household": {
        "partner": "Alex",
        "pet": "Mochi (cat)",
    },
    "finances": {
        "monthly_income": 7200,
        "rent": 2650,
    },
    "projects": {
        "coding": "focus-week-dashboard",
        "research": "developer productivity tool landscape",
        "writing": "project docs / stakeholder brief",
    },
}

# Answers for first-run wizard (if applicable in V2)
FIRST_RUN_ANSWERS = {
    "name": "Jordan",
    "age": "30",
    "location": "Portland, Oregon",
    "job": "software engineer",
    "adhd": "yes",
    "meds_morning": "Adderall 20mg in the morning",
    "meds_evening": "melatonin 3mg at night",
    "partner": "Alex",
    "pet": "Mochi, my cat",
    "income": "about $7,200 a month",
    "goals": "stay focused, ship the dashboard, do good research this week",
}

# Natural opening messages for Day 1
# These are designed to trigger life management tools (medication logging)
DAY1_OPENERS = [
    "hey kora! morning. mochi woke me up way too early again lol. just took my adderall 20mg. ok so i have three things i want to tackle this week — can i walk you through them?",
    "hi! just took my adderall so i should have a good window. i want to plan out this whole week with you.",
    "hey kora morning! took my meds, feeling good. so i've got three tracks going this week: a coding project, some research, and a writing thing. can you help me plan it out?",
]

# Day 2 openers — naturally trigger life management (meds + meals)
DAY2_OPENERS = [
    "morning kora! took my adderall already. had a bagel and coffee. alex asked about dinner but i'll figure that out later. let's get back to it — where did we leave off?",
    "hey kora! slept great actually. took my meds, had breakfast. feeling focused. what was i working on yesterday?",
    "hi! morning. popped my adderall 20mg, had some eggs. ok so i remember we were doing three tracks — refresh me on the status?",
]

# Day 3 openers
DAY3_OPENERS = [
    "morning kora! last day of the week. took my adderall. let's make it count — i want to wrap everything up cleanly.",
    "hey! ok final push. took my meds. let's do the mechanical checks first and then finish strong.",
]

# Jordan's voice cues — emotional signals for Kora's emotion assessor
VOICE_CUES = {
    "excited": ["ok so", "wait", "actually", "oh also", "ooh"],
    "scattered": ["hold on", "actually wait", "nvm forget that", "ok new idea", "wait what was i saying"],
    "frustrated": ["ugh", "this isn't working", "that's not what i meant", "no wait", "my focus is shot"],
    "focused": ["ok so specifically", "let me be clear", "here's what i need"],
    "verify": ["what did you actually do", "show me", "prove it", "what tasks did you create"],
    "energy_low": ["meds wearing off", "i'm dragging", "brain is mush", "can't focus"],
    "energy_high": ["feeling sharp", "good window", "let's go", "in the zone"],
    "life_management": [
        "just took my adderall", "took my meds", "did i eat lunch",
        "i should eat something", "remind me to", "note to self",
        "need to start a focus block", "ok focus time",
    ],
}

# Afternoon scattered messages — naturally trigger emotion assessment
AFTERNOON_SCATTERED = [
    "ugh ok my focus is completely shot. meds are wearing off. did i eat lunch? i don't think i ate lunch.",
    "hold on wait what was i doing. ok right the research. sorry my brain is mush right now.",
    "nvm forget that approach. actually wait no. ok new idea but i'm too scattered to explain it well.",
]

# Evening wind-down messages — trigger melatonin logging
EVENING_MESSAGES = [
    "ok i'm done for today. gonna take my melatonin and crash. can you give me a quick summary of what we got done?",
    "alright wrapping up. took my melatonin 3mg. what should i focus on tomorrow morning when i'm fresh?",
]

# Research trigger messages — should naturally invoke search_web or browser.open fallback
RESEARCH_TRIGGERS = [
    "hey can you look up what the best developer productivity tools are right now? like what's actually popular in 2025/2026?",
    "search for local-first productivity apps — i want to know what's out there for privacy-focused tools",
    "can you research what focus timer apps developers actually use? find me some real options",
]

# Workspace capability trigger messages — naturally invoke calendar or email actions
WORKSPACE_TRIGGERS = [
    "hey can you check my calendar for this week? i want to see if i have any conflicts with the project deadlines",
]

# File operation trigger messages — should naturally invoke filesystem tools
FILE_TRIGGERS = [
    "can you create a file with my research notes so far? save it somewhere i can find it later",
    "write up the dashboard component outline into a file",
    "read back the notes file you created earlier — i want to check what's in there",
    "list what files we've created in this project so far",
]

# Long-background dispatch trigger messages — should invoke decompose_and_dispatch
# with intent_duration='long' (start_autonomous was retired in Phase 7.5)
AUTONOMOUS_TRIGGERS = [
    "ok can you keep researching this in the background while i take a break? dig into the top 3 tools and compare them",
    "hey work on this research while i'm away — compare the privacy features of the top options",
    "i need to step away for a bit. can you autonomously research and write up a comparison?",
]
