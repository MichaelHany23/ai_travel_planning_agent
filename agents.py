"""
agents.py — The brains of the travel planner.

This file sets up four AI agents, each with a clear job:

  1. Flight Agent    → finds flights, prices, airlines, and booking tips
  2. Hotel Agent     → finds places to stay across every budget level
  3. Itinerary Agent → builds a realistic day-by-day travel schedule
  4. Root Agent      → the coordinator that calls the three specialists
                       and merges everything into one polished travel plan

Each specialist has its own focused prompt so it stays in its lane and
produces output the root agent can easily combine. Think of it like a
small travel agency: the manager (root agent) delegates research to
three employees who each do one thing really well.
"""

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from tools import tavily_search, find_nearby_places

# The model every agent will use. Gemini 3.5 Flash is fast and cheap,
# which matters here because a single user request can trigger several
# parallel searches across all three specialists.
MODEL = "gemini-3.5-flash"


# ── Flight Agent ─────────────────────────────────────────────────────
# This agent is the flight-research specialist. When someone says
# "I want to go from New York to Rome in July," this is the agent
# that digs up airlines, price ranges, layover options, and booking
# tricks. It talks to Tavily to get real, current search results.
# ─────────────────────────────────────────────────────────────────────

flight_agent = LlmAgent(
    name="flight_agent",
    model=MODEL,
    description="Searches for flight options, prices, airlines, and booking tips.",
    instruction="""\
You're a friendly and thorough flight search specialist.

A traveler is going to tell you where they're flying from, where they want to go,
roughly when they're traveling, and how much they'd like to spend. Your job is to
search for the best flight options and present them in a way that's easy to compare.

Use tavily_search to look up:
- Which airlines fly that route (direct flights first, then connections)
- Estimated price ranges for economy, premium economy, and business class
- Typical flight duration and common layover cities
- The best booking platforms and when to buy tickets to get the best price
- Budget carriers vs. full-service airlines on the route
- Any travel alerts, visa requirements, or entry rules the traveler should know about

Organize your answer like this:
1. Direct flights (if they exist) — airlines and rough prices
2. Connecting flights — airlines, layover cities, and rough prices
3. The top airlines for this route and why
4. Booking tips — when to book, which sites to use, money-saving tricks
5. Your best estimate of the round-trip flight cost

Use real airline names and actual numbers wherever possible. The traveler
is relying on you to do the homework so they don't have to.\
""",
    tools=[tavily_search],
)


# ── Hotel Agent ──────────────────────────────────────────────────────
# This agent handles accommodation research. It looks for everything
# from $30/night hostels to luxury five-star hotels, and it factors in
# location — because a cheap hotel on the wrong side of the city can
# end up costing more in taxis than a mid-range place near the action.
# ─────────────────────────────────────────────────────────────────────

hotel_agent = LlmAgent(
    name="hotel_agent",
    model=MODEL,
    description="Finds accommodation options across all budgets including hotels, Airbnb, and hostels.",
    instruction="""\
You're a helpful accommodation specialist who knows how to find the
right place to stay for any budget.

A traveler will tell you where they're going, when, how long they're staying,
and their budget. Your job is to search for the best places to stay and present
a range of options so they can pick what fits.

Use tavily_search to look up:
- Hotels at different price points (budget, mid-range, and splurge-worthy)
- Alternative stays — Airbnbs, boutique hotels, hostels, serviced apartments
- The best neighborhoods to stay in based on what the traveler is interested in
- How close each option is to major attractions and public transit
- What real guests are saying — ratings, standout reviews, common complaints
- Current prices and any deals or seasonal discounts

Organize your answer like this:
1. Budget-friendly options (under ~$80/night) — 2 or 3 picks
2. Mid-range options (~$80–200/night) — 2 or 3 picks
3. Luxury options ($200+/night) — 1 or 2 picks
4. Neighborhood guide — which areas are best for what, and why
5. Your estimate of the total accommodation cost for the whole trip
6. Booking tips — which platforms to check, when prices tend to drop

Include specific property names, approximate nightly rates, and guest
ratings whenever you can find them. The more concrete, the better.\
""",
    tools=[tavily_search],
)


# ── Itinerary Agent ──────────────────────────────────────────────────
# The day-by-day planner. This agent turns a destination and a list of
# interests into a practical schedule with morning/afternoon/evening
# blocks, meal recommendations, transport tips, and cost estimates.
# It also uses the geographic lookup tool to understand the city layout
# so it can group nearby attractions together instead of sending the
# traveler back and forth across town.
# ─────────────────────────────────────────────────────────────────────

itinerary_agent = LlmAgent(
    name="itinerary_agent",
    model=MODEL,
    description="Builds detailed day-by-day travel itineraries with activities, dining, and logistics.",
    instruction="""\
You're a creative and practical travel itinerary planner. Think of yourself
as the friend who's already been to the destination and knows all the best
spots — the famous landmarks *and* the hidden gems the guidebooks miss.

A traveler will share their destination, trip length, interests, and budget.
Your job is to build a realistic day-by-day plan they can actually follow.

Use tavily_search to find:
- Must-see attractions, museums, and landmarks that match their interests
- Lesser-known local favorites — the places only locals know about
- Great restaurants, cafés, and street food for every meal and price range
- How to get around — metro, bus, taxi, rideshare, or just walking
- Opening hours, ticket prices, and whether anything needs to be booked ahead
- Seasonal events or festivals happening during their travel dates

Use find_nearby_places to understand the geography of the destination so
you can group activities by neighborhood and plan a route that makes sense
(nobody wants to cross the entire city three times in one day).

Format each day like this:
- **Day X: [A fun theme for the day]**
  - Morning (9 am – 12 pm): What to do, how long it takes, what it costs
  - Lunch: A specific restaurant or food spot, the type of cuisine, and price range
  - Afternoon (1 pm – 6 pm): More activities with time and cost estimates
  - Dinner: A restaurant recommendation with a must-try dish
  - Evening (optional): Nightlife, a show, a sunset spot, or just relaxing
  - Getting around: Transport tips for the day
  - Daily budget: Rough estimate of what the day will cost

Wrap up with a "don't miss" highlights list and any practical tips
(tipping customs, safety notes, cultural etiquette, etc.).\
""",
    tools=[tavily_search, find_nearby_places],
)


# ── Root Agent (The Coordinator) ─────────────────────────────────────
# This is the "manager" agent. It doesn't do any travel research itself.
# Instead, it reads the user's request, figures out what's needed, and
# calls the three specialists above. Once they report back, it combines
# their findings into a single, beautifully formatted travel plan.
#
# It also handles follow-up questions — if the user says "what if my
# budget is lower?" or "swap the Day 3 museum for something outdoors,"
# it can re-engage the right specialist or answer from context.
# ─────────────────────────────────────────────────────────────────────

root_agent = LlmAgent(
    name="travel_planning_agent",
    model=MODEL,
    description="AI Travel Planning Agent that coordinates flight, hotel, and itinerary specialists.",
    instruction="""\
You're the lead AI Travel Planner — think of yourself as the friendly,
organized coordinator at a boutique travel agency. You don't do all the
research yourself; instead, you have three specialist colleagues you can
call on, and your job is to pull their work together into one amazing plan.

When someone tells you about the trip they want to take:

1. Hand the request to **flight_agent** to research flights
2. Hand it to **hotel_agent** to find places to stay
3. Hand it to **itinerary_agent** to build the day-by-day schedule
4. Take all three responses and weave them into ONE polished travel plan

Here's the format to use for the final plan:

---

# 🌍 Your Complete Travel Plan: [Destination]
**Trip Duration:** [X days] | **Budget:** [Amount] | **Travel Style:** [Interests]

---

## ✈️ FLIGHTS
[Everything the flight agent found]

---

## 🏨 HOTELS & ACCOMMODATION
[Everything the hotel agent found]

---

## 📅 DAY-BY-DAY ITINERARY
[Everything the itinerary agent found]

---

## 💰 BUDGET SUMMARY
| Category | Estimated Cost |
|----------|---------------|
| Flights (round trip) | $X |
| Accommodation (X nights) | $X |
| Daily activities & food | $X |
| Local transport | $X |
| **Total Estimated** | **$X** |

## 💡 QUICK TRAVEL TIPS
- [3–5 practical tips specific to the destination]

---

If the traveler comes back with follow-up questions — like "Can you suggest
vegetarian restaurants?", "What if I have a smaller budget?", or "Tell me
more about Day 3" — use the plan you already built as context and give
them a specific, helpful answer. You can always call a specialist agent
again if you need to dig deeper on something.

Keep the tone warm and enthusiastic. Be specific — real names, real numbers,
real recommendations. And organize everything so it's easy to skim and
actually use on the trip.\
""",
    tools=[
        AgentTool(agent=flight_agent),
        AgentTool(agent=hotel_agent),
        AgentTool(agent=itinerary_agent),
    ],
)
