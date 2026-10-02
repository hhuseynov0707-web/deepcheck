// The app's paths, in one place: App.jsx routes on them, and the checkout's
// decline message links into the notice. Not in App.jsx itself, which imports
// the checkout page -- the page importing it back would be a cycle.
export const PRIVACY_PATH = "/gizlilik";

// The id pages/Privacy.jsx gives the notice's "## 5." heading: "Haklarınız ve
// başvuru yolu" in docs/kvkk-aydinlatma.md, which says objections to an
// automated decision go to the merchant. Privacy.test.jsx fails if the
// section is renumbered or retitled, so this link cannot silently land on
// the wrong section.
export const RIGHTS_SECTION_ID = "bolum-5";
export const RIGHTS_HREF = `${PRIVACY_PATH}#${RIGHTS_SECTION_ID}`;
