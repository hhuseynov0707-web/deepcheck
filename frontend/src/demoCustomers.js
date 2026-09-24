// The SYNTHETIC demo customers that backend/demo_seed.py seeds into the reserved
// "demo" merchant namespace, for the Demo page's customer selector.
//
// None of them is a person. Each is a simulator identity
// (train_model.simulate_identity_sessions) with a stored history of
// SYNTHETIC_SESSIONS_PER_MODALITY sessions per input type, mouse and keyboard,
// because the team has no customer base to take a history from. Every label
// built from this list says "sentetik", and the references say it too.
//
// backend/test_demo.py pins this list and the count to demo_seed.DEMO_CUSTOMERS
// and demo_seed.SESSIONS_PER_MODALITY, so a customer renamed or re-seeded on one
// side cannot leave the page promising a history the database does not hold.
export const SYNTHETIC_SESSIONS_PER_MODALITY = 20;

export const SYNTHETIC_DEMO_CUSTOMERS = [
  { ref: "sentetik-ayse", name: "Ayşe" },
  { ref: "sentetik-mehmet", name: "Mehmet" },
  { ref: "sentetik-zeynep", name: "Zeynep" },
];

export function syntheticCustomerLabel(customer) {
  const n = SYNTHETIC_SESSIONS_PER_MODALITY;
  // Short enough to show whole in the select at the Demo page's width.
  return `${customer.name} — sentetik geçmiş (fare ${n}, klavye ${n})`;
}
