"""Shared exact selectors used by LinkedIn and Sales Navigator browser adapters."""

SALES_NAV_PEOPLE_RESULT_ROW = "li.artdeco-list__item:has(a[href*='/sales/lead/'])"
SALES_NAV_PROFILE_LINK = "a[href*='/sales/lead/']"
SALES_NAV_MORE_ACTIONS_BUTTON = 'button[aria-label^="See more actions for"]'
SALES_NAV_OPEN_ACTIONS_BUTTON = 'button[aria-label="Open actions overflow menu"]'
LINKEDIN_DIALOG = "[role='dialog'], .artdeco-modal, [data-test-modal]"
MESSAGE_COMPOSER = "div[role='textbox'][contenteditable='true'], textarea[name='message']"

__all__ = [
    "LINKEDIN_DIALOG",
    "MESSAGE_COMPOSER",
    "SALES_NAV_MORE_ACTIONS_BUTTON",
    "SALES_NAV_OPEN_ACTIONS_BUTTON",
    "SALES_NAV_PEOPLE_RESULT_ROW",
    "SALES_NAV_PROFILE_LINK",
]
