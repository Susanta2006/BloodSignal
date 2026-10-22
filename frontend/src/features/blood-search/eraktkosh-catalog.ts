/** Local selector catalogs for the observed e-RaktKosh bulk endpoint.
 * Source names and IDs are retained exactly; display labels may correct spelling independently.
 */
export const eraktkoshStates = [
  { sourceName: "Tripura", displayName: "Tripura", id: "16" },
  { sourceName: "Andaman and Nicobar Islands", displayName: "Andaman and Nicobar Islands", id: "35" },
  { sourceName: "Andhra Pradesh", displayName: "Andhra Pradesh", id: "28" },
  { sourceName: "Arunachal Pradesh", displayName: "Arunachal Pradesh", id: "12" },
  { sourceName: "Assam", displayName: "Assam", id: "18" },
  { sourceName: "Bihar", displayName: "Bihar", id: "10" },
  { sourceName: "Chandigarh", displayName: "Chandigarh", id: "94" },
  { sourceName: "Chattisgarh", displayName: "Chhattisgarh", id: "22" },
  { sourceName: "Dadra And Nagar Haveli And Daman And Diu", displayName: "Dadra and Nagar Haveli and Daman and Diu", id: "25" },
  { sourceName: "Delhi", displayName: "Delhi", id: "97" },
  { sourceName: "Goa", displayName: "Goa", id: "30" },
  { sourceName: "Gujarat", displayName: "Gujarat", id: "24" },
  { sourceName: "Haryana", displayName: "Haryana", id: "96" },
  { sourceName: "Himachal Pradesh", displayName: "Himachal Pradesh", id: "92" },
  { sourceName: "Jammu and Kashmir", displayName: "Jammu and Kashmir", id: "91" },
  { sourceName: "Jharkhand", displayName: "Jharkhand", id: "20" },
  { sourceName: "Karnataka", displayName: "Karnataka", id: "29" },
  { sourceName: "Kerala", displayName: "Kerala", id: "32" },
  { sourceName: "Ladakh", displayName: "Ladakh", id: "37" },
  { sourceName: "Lakshadweep", displayName: "Lakshadweep", id: "31" },
  { sourceName: "Madhya Pradesh", displayName: "Madhya Pradesh", id: "23" },
  { sourceName: "Maharastra", displayName: "Maharashtra", id: "27" },
  { sourceName: "Manipur", displayName: "Manipur", id: "14" },
  { sourceName: "Meghalaya", displayName: "Meghalaya", id: "17" },
  { sourceName: "Mizoram", displayName: "Mizoram", id: "15" },
  { sourceName: "Nagaland", displayName: "Nagaland", id: "13" },
  { sourceName: "Odisha", displayName: "Odisha", id: "21" },
  { sourceName: "Puducherry", displayName: "Puducherry", id: "34" },
  { sourceName: "Punjab", displayName: "Punjab", id: "93" },
  { sourceName: "Rajasthan", displayName: "Rajasthan", id: "98" },
  { sourceName: "Sikkim", displayName: "Sikkim", id: "11" },
  { sourceName: "Tamil Nadu", displayName: "Tamil Nadu", id: "33" },
  { sourceName: "Telengana", displayName: "Telangana", id: "36" },
  { sourceName: "Uttarakhand", displayName: "Uttarakhand", id: "95" },
  { sourceName: "Uttar Pradesh", displayName: "Uttar Pradesh", id: "99" },
  { sourceName: "West Bengal", displayName: "West Bengal", id: "19" },
] as const;

export const eraktkoshComponents = [
  { value: "cryo_poor_plasma", sourceName: "Cryo Poor Plasma", displayName: "Cryo Poor Plasma", id: "21" },
  { value: "cryoprecipitate", sourceName: "Cryoprecipitate", displayName: "Cryoprecipitate", id: "17" },
  { value: "fresh_frozen_plasma", sourceName: "Fresh frozen Plasma", displayName: "Fresh Frozen Plasma", id: "13" },
  { value: "irradiated_rbc", sourceName: "Irradiated RBC", displayName: "Irradiated RBC", id: "29" },
  { value: "leukoreduced_rbc", sourceName: "Leukoreduced Rbc", displayName: "Leukoreduced RBC", id: "30" },
  { value: "prbc", sourceName: "Packed Red Blood Cells", displayName: "Packed Red Blood Cells", id: "12" },
  { value: "plasma", sourceName: "Plasma", displayName: "Plasma", id: "19" },
  { value: "platelet_concentrate", sourceName: "Platelet Concentrate", displayName: "Platelet Concentrate", id: "20" },
  { value: "platelet_rich_plasma", sourceName: "Platelate Rich Plasma", displayName: "Platelet Rich Plasma", id: "16" },
  { value: "random_donor_platelets", sourceName: "Random donor platelets", displayName: "Random Donor Platelets", id: "23" },
  { value: "sagm_packed_red_blood_cells", sourceName: "Sagm Packed Red Blood Cells", displayName: "SAGM Packed Red Blood Cells", id: "28" },
  { value: "single_donor_plasma", sourceName: "Single donor Plasma", displayName: "Single Donor Plasma", id: "18" },
  { value: "single_donor_platelets", sourceName: "Single Donor Platelets", displayName: "Single Donor Platelets", id: "14" },
  { value: "whole_blood", sourceName: "Whole Blood", displayName: "Whole Blood", id: "11" },
] as const;

export const componentOptions = eraktkoshComponents;

export function componentDisplayName(value: string): string {
  return eraktkoshComponents.find((item) => item.value === value)?.displayName ?? value;
}
