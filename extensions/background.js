function contactCheckerBackgroundLog(
  event,
  details = {}
) {
  const timestamp =
    new Date().toISOString();

  console.log(
    `[ContactChecker BG] ${timestamp} ${event}`,
    details
  );
}


chrome.action.onClicked.addListener(
  async (tab) => {
    if (!tab.id) return;

    contactCheckerBackgroundLog(
      "EXTENSION_TOGGLE_CLICKED",
      {
        tabId: tab.id,
        url: tab.url || ""
      }
    );

    try {
      await chrome.scripting.executeScript({
        target: {
          tabId: tab.id
        },
        files: ["content.js"]
      });

      contactCheckerBackgroundLog(
        "CONTENT_SCRIPT_INJECTED",
        {
          tabId: tab.id
        }
      );

    } catch (error) {
      console.error(
        "Could not activate Contact Checker:",
        error
      );

      contactCheckerBackgroundLog(
        "CONTENT_SCRIPT_ERROR",
        {
          error: error?.message || String(error)
        }
      );
    }
  }
);


chrome.runtime.onMessage.addListener(
  (message, sender, sendResponse) => {

    let endpoint;
    let body;

    if (message.type === "CHECK_EMAILS") {
      endpoint = "/check";

      body = {
        emails: message.emails
      };
    }

    else if (message.type === "MATCH_APOLLO") {
      endpoint = "/match-apollo";

      body = {
        contacts: message.contacts
      };
    }

    else {
      return;
    }


    const startedAt =
      performance.now();

    contactCheckerBackgroundLog(
      "API_REQUEST_STARTED",
      {
        endpoint,
        contactCount:
          Array.isArray(
            body.contacts
          )
            ? body.contacts.length
            : undefined,
        emailCount:
          Array.isArray(
            body.emails
          )
            ? body.emails.length
            : undefined
      }
    );


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

        const durationMs =
          Math.round(
            performance.now() - startedAt
          );

        contactCheckerBackgroundLog(
          "API_REQUEST_COMPLETE",
          {
            endpoint,
            durationMs,
            summary:
              data.summary || null
          }
        );

        sendResponse({
          success: true,
          results: data.results,
          activity:
            data.activity || [],
          summary:
            data.summary || {}
        });

      })

      .catch((error) => {

        const durationMs =
          Math.round(
            performance.now() - startedAt
          );

        console.error(
          "Contact Checker API error:",
          error
        );

        contactCheckerBackgroundLog(
          "API_REQUEST_ERROR",
          {
            endpoint,
            durationMs,
            error:
              error?.message ||
              String(error)
          }
        );

        sendResponse({
          success: false,
          error: error.message
        });

      });


    return true;
  }
);
