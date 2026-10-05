import os
from tavily import TavilyClient
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable


def tavily_search(query: str) -> str:
    """Run a web search using Tavily to pull in fresh, real-world travel info.

    I'm using Tavily here because it's built specifically for AI agents — it
    returns clean, summarized results instead of raw HTML, which makes it way
    easier for the language model to work with.

    Args:
        query: Whatever we want to look up — flights, hotels, restaurant
               recommendations, visa requirements, you name it.

    Returns:
        A nicely formatted string with the top search results (title, URL,
        and a content snippet for each). If Tavily also generates a quick
        summary answer, that goes at the top.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not configured."
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="advanced",
            max_results=5,
            include_answer=True,
        )

        # Build a readable block of text the agent can reason over.
        parts = []
        if response.get("answer"):
            parts.append(f"Summary: {response['answer']}\n")
        for r in response.get("results", []):
            # Cap each snippet so the context window isn't overwhelmed.
            snippet = r.get("content", "")[:600].strip()
            parts.append(f"**{r['title']}**\nURL: {r['url']}\n{snippet}")
        return "\n\n---\n\n".join(parts) if parts else "No results found."
    except Exception as e:
        return f"Search error: {e}"


def find_nearby_places(location: str, category: str = "tourist attractions") -> str:
    """Look up a place on OpenStreetMap to get its coordinates and address info.

    The itinerary agent uses this to understand *where* a destination actually
    is before it starts planning the day-by-day schedule. Knowing the lat/long
    and country helps it organize activities geographically so the traveler
    isn't zigzagging across the city all day.

    Args:
        location: A city, landmark, or address — for example "Paris, France"
                  or "Shibuya Crossing, Tokyo".
        category: The kind of places the traveler is interested in (e.g.
                  "museums", "restaurants", "parks"). This is passed along as
                  context but the actual nearby-place search is handled by
                  Tavily in a separate call.

    Returns:
        A short summary with the full address, coordinates, country, and
        region — or a helpful error message if the lookup fails.
    """
    geolocator = Nominatim(user_agent="ai-travel-planning-agent/1.0", timeout=10)
    try:
        geo = geolocator.geocode(location, addressdetails=True)
        if not geo:
            return f"Could not find geographic data for: {location}"

        address = geo.raw.get("address", {})
        details = {
            "Location": location,
            "Full Address": geo.address,
            "Latitude": f"{geo.latitude:.5f}",
            "Longitude": f"{geo.longitude:.5f}",
            "Country": address.get("country", "N/A"),
            "State/Region": address.get("state", address.get("county", "N/A")),
            "Category Searched": category,
        }
        lines = [f"{k}: {v}" for k, v in details.items()]
        return "\n".join(lines)
    except GeocoderTimedOut:
        return f"Geocoding timed out for: {location}. Try a more specific location name."
    except GeocoderUnavailable:
        return "Nominatim service temporarily unavailable. Please retry."
    except Exception as e:
        return f"Location lookup error: {e}"
