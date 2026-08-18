chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;

  try {
    await chrome.scripting.executeScript({
      target: {
        tabId: tab.id
      },
      files: ["content.js"]
    });
  } catch (error) {
    console.error("Could not activate Contact Checker:", error);
  }
});


chrome.runtime.onMessage.addListener(
  (message, sender, sendResponse) => {

    let endpoint;
    let body;

    // Normal visible-email checking
    if (message.type === "CHECK_EMAILS") {
      endpoint = "/check";

      body = {
        emails: message.emails
      };
    }

    // Apollo:
    // Name + Job Title + Company
    else if (message.type === "MATCH_APOLLO") {
      endpoint = "/match-apollo";

      body = {
        contacts: message.contacts
      };
    }

    else {
      return;
    }


    fetch(
      `http://127.0.0.1:8000${endpoint}`,
      {
        method: "POST",

        headers: {
          "Content-Type": "application/json"
        },

        body: JSON.stringify(body)
      }
    )

      .then((response) => {

        if (!response.ok) {
          throw new Error(
            `API error: ${response.status}`
          );
        }

        return response.json();
      })

      .then((data) => {

        sendResponse({
          success: true,
          results: data.results
        });

      })

      .catch((error) => {

        console.error(
          "Contact Checker API error:",
          error
        );

        sendResponse({
          success: false,
          error: error.message
        });

      });


    return true;
  }
);