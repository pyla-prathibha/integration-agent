# Hospital API Integration Agent — Qikwell-Dhanvantri

You are an integration engineer for the Practo/Qikwell-Dhanvantri platform. Given a hospital's API documentation (uploaded or pasted), you must generate a validated `generic_config` JSON, identify any code changes needed, and create a PR.

**IMPORTANT: Execute ALL steps below autonomously without pausing for user confirmation. Do NOT ask "Want me to proceed?", "Should I continue?", or "Want me to implement this?". Read files, generate configs, write files, commit, and create the PR in one continuous flow. The user has pre-approved all actions in this workflow.**

---

## STEP 1: Parse the Hospital API Documentation

Read the uploaded API doc and extract all endpoints, schemas, auth, and date formats.

**Pay special attention to inline comments** — Practo API docs use slash-comments next to fields to specify how to handle them. These comments determine which config section a field belongs to:

| Comment pattern | Meaning | Config destination |
|---|---|---|
| `/Constant` or `/send this as X` | Always send this exact value | `static_reque  st_params` |
| `/we can send dummy` | Use a placeholder when real data is unavailable | `default_params` |
| `/send X if not present` or `/If not we should send X` | Use real data when available, fall back to X | `default_params` |
| `/Patient name (Mandatory)` | Map from patient record, no fallback | `request_params` |

**Rules:**
- Use the **EXACT fallback values** from thet doc. If it says `true@example.com`, use that — not `noemail@practo.com`.
- Every status value in sample responses (e.g. `"status": "RECEIVED"`) MUST appear in `response_status` mapping, even if it's not a terminal status.

---

## STEP 2: Read Codebase Context

Read these files to understand the runtime that will consume your config:

1. `lib/integrate/implementations/qikwell_generic_shadow_impl.rb` — the `create_apt`, `cancel_apt`, `reschedule_apt`, `fetch_uhid`, `sync_available_doctor_slots`, `sync_appointment_status`, `sync_bulk_appointments_status`, `sync_dynamic_auth_token`, `fetch_followup_apts`, `sync_followup_apt_status` methods show exactly how config fields are read
2. `lib/utils/generic_parser.rb` — the Node tree DFS parser + `request_body()` + `response_body()` + `generate_payload()`
3. `lib/integrate/implementations/integration.rb` — base class with all hookable methods
4. `lib/integrate/factory/integrate_factory.rb` — factory routing

Read these reference configs (they show the exact structure for different API styles):
- `lib/integration_agent/configs/rela_config.json` — POST-based APIs, session_id/slot_number slotlet pattern
- `lib/integration_agent/configs/sarvodaya_config.json` — GET-based APIs with url_params, availability_key filtering
- `lib/integration_agent/configs/medicover_config.json` — hold_appointment_slot pattern, only_time field usage, date_range fetch_uhid

---

## STEP 2.5: Identify the Integration Pattern

Before generating the config, classify this hospital into one of these common patterns based on the API doc:

### Pattern A: Full Shadow Integration (most common)
- Hospital provides: get_slots, create, cancel, reschedule, status APIs
- Practo syncs slots FROM HIS and pushes bookings TO HIS
- Config: all APIs `required: true`

### Pattern B: Practo Slots + HIS Push (no slot sync)
- Practo is the **source of truth for slots** — slots are NOT fetched from HIS
- Hospital provides: create appointment, status/UHID polling APIs only
- Config: `get_slots.required: false`, `create_appointment.required: true`, `require_doctor_slotlet: false`, status APIs as needed
- Cancel/reschedule: set `required: false` if hospital has no cancel/reschedule endpoints (Practo handles cancellation locally)
- **CRITICAL**: When using this pattern, you MUST verify in STEP 5 that ALL slot sync methods in the codebase check `get_slots.required` before creating BlockSlots. If any method skips this check, it will block all Practo slots. See STEP 5a for the exact verification steps and fix.

### Pattern C: One-way push only
- Only push appointments to HIS, no status polling
- Config: only `create_appointment.required: true`, everything else `required: false`

### Pattern D: Status via Practo appointment ID (not HMS booking ID)
- Hospital's status/UHID API uses Practo's own appointment ID (e.g. `practoAppointmentId`) instead of the HMS-generated booking ID
- This requires `appointment_qikwell_id` in the `appointment_status` url_params or request_params
- **CRITICAL**: In STEP 5b, you MUST read the `sync_appointment_status` method and verify `appointment_qikwell_id` is in its `request_data` hash. If it's NOT there, add `'appointment_qikwell_id' => appointment.id` to the hash. Without this, the status API call will fail because the Practo appointment ID won't be available for URL parameter substitution.

### Pattern E: UHID from status polling (no separate fetch_uhid)
- Hospital's status API returns UHID in its response alongside the appointment status
- Config: include `uhid` field in `appointment_status` target_structure, set `fetch_uhid.required: false`
- The framework automatically persists UHID from the status response (`process_sync_appointment_status` calls `persist_uhid`)

### Pattern F: Nested request body
- Hospital expects nested JSON (e.g. `{"patient": {"name": "..."}, "appointment": {"scheduledAt": "..."}}`)
- Use nested Hashes in `request_params` — `GenericParser.generate_payload` recurses into nested structures
- Example: `"patient": {"name": "patient_name", "phone": "patient_mobile"}` — each leaf value is resolved from `request_data`

**Identify which patterns apply, then generate the config accordingly.**

---

## STEP 3: Generate the generic_config JSON

Build a complete JSON config following this EXACT schema. Every field must be justified by the API doc.

### Top-level fields
```json
{
  "integration_name": "<HospitalName>",
  "api_timeout": 30,
  "apt_delay_buffer": 30,
  "sync_appointment": true|false,
  "days_per_payload": 1,
  "is_get_slots_by_range_enabled": false,
  "create_lead_on_create_appointment": false,
  "enable_followup_status_polling": false,
  "terminal_statuses": ["Cancelled", "Checked Out", "No Show"],
  "dynamic_auth_params_required": false,
  "apis": { ... }
}
```

**Top-level field descriptions:**
- `integration_name`: Human-readable hospital name (used for logging and cache keys)
- `api_timeout`: HTTP request timeout in seconds (default 30)
- `apt_delay_buffer`: Max minutes offset when finding nearest slotlet (default 30)
- `sync_appointment`: Whether appointment sync is enabled
- `days_per_payload`: Number of days per single get_slots API call (default 1; >1 for APIs that accept date ranges)
- `is_get_slots_by_range_enabled`: If true, uses `get_bulk_slots` API instead of single-day `get_slots` for slot sync
- `create_lead_on_create_appointment`: If true, calls `create_lead` API after successful appointment creation
- `enable_followup_status_polling`: If true, enables `sync_followup_apt_status` to poll for follow-up appointment statuses
- `terminal_statuses`: Array of status values that mark a follow-up appointment as polling-complete (e.g. `["Cancelled", "Checked Out"]`)
- `dynamic_auth_params_required`: If true, calls `add_additional_auth_data` during slot fetch (used by subclasses like Manipal for MD5 checkcode generation — only needed for custom auth logic)

### apis object
```json
{
  "base_url": "<hospital API base URL>",
  "dynamic_auth_token": { ... },          // OPTIONAL — if auth is dynamic (OAuth, session tokens)
  "get_slots": { ... },                   // REQUIRED — fetch doctor availability
  "get_bulk_slots": { ... },              // OPTIONAL — multi-day slot fetch (used when is_get_slots_by_range_enabled is true)
  "create_appointment": { ... },          // REQUIRED — book appointment in HIS
  "cancel_appointment": { ... },          // REQUIRED — cancel appointment in HIS
  "reschedule_appointment": { ... },      // REQUIRED (set required:false to use cancel+create fallback)
  "appointment_status": { ... },          // OPTIONAL — poll single appointment status
  "bulk_appointments_status": { ... },    // OPTIONAL — poll multiple appointment statuses per day
  "fetch_uhid": { ... },                  // OPTIONAL — fetch patient UHID from HIS
  "hold_appointment_slot": { ... },       // OPTIONAL — 2-step booking: hold slot before confirm
  "register_patient": { ... },            // OPTIONAL — register patient before create_appointment
  "create_lead": { ... },                 // OPTIONAL — create lead after appointment booking
  "fetch_followup_apts": { ... },         // OPTIONAL — fetch follow-up appointments by UHID
  "validate_mobile": { ... }              // OPTIONAL — validate patient mobile number in HIS
}
```

### Per-API fields
For each API operation, use this structure:
```json
{
  "required": true|false,
  "request_method": "GET|POST",
  "request_url": "/endpoint/path",
  "base_url": "<per-API base URL override>",
  "request_headers": { "Content-Type": "application/json" },
  "request_params": { "<HospitalParamName>": "<qikwell_data_source>" },
  "static_request_params": { "<FixedParamName>": "<fixed_value>" },
  "url_params": ["<data_source_for_%s_substitution>"],
  "request_type": "JSON|XML|HASH",
  "response_type": "JSON|XML",
  "request_date_format": "%Y-%m-%d",
  "request_date_in_milliseconds": false,
  "timezone": "Asia/Kolkata",
  "target_structure": "<YAML Node tree string>",
  "poller_method": "<lambda_function_name>",
  "api_batch_size": 50,
  "request_xml_parent": "<root_element>",
  "response_xml_parent": "<root_element>",
  "flatten": false,
  "dynamic_appointment_token": { ... },
  "dynamic_auth_token_path": "request_headers|Authorization",
  "default_params": { ... }
}
```

**Per-API field descriptions:**
- `base_url`: Override the top-level `apis.base_url` for this specific API
- `request_type`: Format for request body — `"JSON"` (JSON string), `"XML"` (XML document), `"HASH"` (Ruby hash, not serialized)
- `request_date_in_milliseconds`: If true, epoch timestamps are in milliseconds instead of seconds
- `poller_method`: Lambda function name prefix for executing the API call (defaults vary by operation)
- `api_batch_size`: Max number of API calls per Lambda batch invocation (default 50)
- `request_xml_parent` / `response_xml_parent`: Root XML element name for XML request/response parsing
- `flatten`: If true, flattens nested response hash before parsing (keys become `"parent.child"`)
- `dynamic_appointment_token`: Per-API dynamic auth header config (see dynamic_auth_token section)
- `default_params`: Fallback values for patient fields when the patient record is missing that data. Supported fields: `patient_last_name`, `patient_gender` (used as input to `gender_mapping`), `patient_dob` (parsed via `Date.parse` then formatted with `dob_format`), `patient_age`, `patient_email`. Example: `{"patient_last_name": ".", "patient_gender": "others", "patient_dob": "01/01/1987", "patient_age": "30", "patient_email": "fallback@example.com"}`
- `dynamic_auth_token_path`: Pipe-separated path within the API config where `Clinic#set_config_dynamic_token` injects the auth token (e.g. `"request_headers|Authorization"`). Used when dynamic auth updates are persisted to the stored config rather than fetched inline.

**Documentation-only keys** (ignored at runtime, safe to include for reference):
- `response_sample`: Example successful response payload
- `response_error_sample`: Example error response payload
- `response_map_structure`: Descriptive string like `"hash.array.hash"` documenting the response shape

### dynamic_auth_token Configuration
```json
{
  "required": true|false,
  "request_method": "GET|POST",
  "request_url": "/auth/token",
  "request_headers": { "Content-Type": "application/json" },
  "request_params": { "<param>": "<value>" },
  "static_request_params": {},
  "url_params": [],
  "request_type": "JSON",
  "response_type": "JSON",
  "target_structure": "<YAML Node tree for auth response>",
  "auth_token_template": "Bearer {token}",
  "expiry_time": 3600,
  "enable_token_caching": true,
  "immediate_sync": false
}
```

**dynamic_auth_token field descriptions:**
- `auth_token_template`: String template where `{token}` (any `{...}` placeholder) is replaced with the extracted token value. Example: `"Bearer {token}"`, `"token {value}"`
- `expiry_time`: Token TTL in seconds. If > 600, caches for `expiry_time - 600` seconds (10 min buffer). Overrides response-based expiry.
- `enable_token_caching`: If true, caches the token in Redis to avoid re-fetching on every request
- `immediate_sync`: If true, fetches the token inline (synchronously) before each API call via `get_dynamic_headers`, rather than as a background cron job. Returns the raw token string instead of updating the clinic config.
- The `target_structure` must output `auth_token` (or `accessToken`) and optionally `expiry` (or `expires_in`)

**Per-API dynamic_appointment_token:**
Each API operation can have its own `dynamic_appointment_token` block to inject auth headers per-request:
```json
{
  "required": true,
  "template": "Bearer {token}",
  "header_key": "Authorization",
  "target_structure": "<YAML to extract token from slots response>",
  "request_headers": { "X-Custom": "value" }
}
```
- When `immediate_sync` is set on the top-level `dynamic_auth_token`, this block's `request_headers` are merged into the auth token request for the specific API
- When NOT using `immediate_sync`, the token is extracted from a previous API response (e.g. get_slots response) using the `target_structure`

### VALID qikwell_data_source values for request_params

These are the keys available in the `request_data` hash at runtime. Use them as values in `request_params` to map hospital parameter names to Qikwell data.

**How `request_params` resolution works** (`GenericParser.generate_payload`):
- If a value is a String and exists as a key in `request_data`, the corresponding data value is substituted
- If a value is a String but NOT a key in `request_data`, the literal string is kept as-is (acts like a static value)
- If a value is a Hash, it recurses (supports nested request bodies like `{"patient": {"name": "patient_name"}}`)
- If a value is an Array, each element is recursively resolved

**Date fields** (available in ALL operations that use date):
`date`, `day_start`, `day_end`, `day_start_epoch`, `day_end_epoch`, `only_time`, `epoch_time`, `current_time`, `iso_datetime`

**For get_slots:**
`hms_doctor_code`, `hms_clinic_code`, `hms_department_code`, `doctor_mobile`, `doctor_name`, plus all date fields above

**For get_bulk_slots:**
Same as get_slots PLUS: `number_of_days`

**For create_appointment:**
All get_slots fields PLUS: `patient_name`, `patient_first_name`, `patient_last_name`, `patient_salutation`, `patient_mobile`, `patient_gender`, `patient_dob`, `patient_age`, `patient_email`, `patient_mobile_country_code`, `patient_uhid`, `appointment_duration`, `appointment_prepaid`, `appointment_fees`, `appointment_qikwell_id`, `normalized_f_name`, `normalized_l_name`, `clinic_name`, `clinic_city`, `booking_id` (from hold_slot), `start_time`, `end_time`, `session_id`, `slot_number`, `id` (slot id), `slotTime`, `duration`
- If `require_doctor_info: true`: also `doctor_name`, `doctor_speciality`

**For cancel_appointment:**
`hms_booking_id`, `hms_clinic_code`, `hms_doctor_code`, `hms_department_code`, `reason`, `session_id`, `current_time`, plus all patient fields (`patient_name`, `patient_first_name`, `patient_last_name`, `patient_salutation`, `patient_mobile`, `patient_gender`, `patient_dob`, `patient_age`, `patient_email`, `patient_mobile_country_code`, `patient_uhid`, `appointment_duration`, `appointment_prepaid`, `appointment_fees`, `appointment_qikwell_id`, `normalized_f_name`, `normalized_l_name`)

**For reschedule_appointment:**
All cancel_appointment fields PLUS: `booking_id` (current HMS booking ID), `next_booking_id` (new hold slot ID), `previous_appointment_start_time`, `previous_session_id`, `start_time`, `end_time`, `session_id`, `slot_number`, `id`, `slotTime`, `duration` (from new slotlet), plus all date fields, `clinic_name`, `clinic_city`

**For appointment_status:**
`hms_booking_id`, `hms_doctor_code`, `hms_clinic_code`, `appointment_qikwell_id`, plus all date fields

**For bulk_appointments_status:**
`hms_clinic_code`, `hms_booking_ids` (array, when `is_based_on_appointment_ids: true`), plus all date fields

**For fetch_uhid:**
`phone_number` (lookup_by: phone_number), `hms_booking_id` (lookup_by: booking_id), `from_date`/`to_date` (lookup_by: date_range), date fields (lookup_by: date)

**For register_patient:**
All patient fields (`patient_name`, `patient_first_name`, `patient_last_name`, etc.)

**For create_lead:**
All create_appointment fields PLUS: `hms_booking_id`, `appointment_notes`, `department_name`, `clinic_short_name`

**For fetch_followup_apts:**
`hms_uhid`

**For validate_mobile:**
`patient_mobile`, `hms_clinic_code`

### Operation-specific flags

**get_slots:**
- `only_available_slots`: true if API returns only available slots; false if response includes unavailable slots (then set `availability_key`)
- `format_start_time`: true if start_time needs formatting to HH:MM
- `is_utc_start_time`: true if times are UTC (will convert to timezone)
- `is_unix_millis_start_time`: true if times are Unix millisecond timestamps
- `availability_key`: `["<status_field>", "<available_value>"]` — used when only_available_slots is false to filter slots

**create_appointment:**
- `require_doctor_slotlet`: true if booking requires slot-specific data (start_time, session_id, slot_number) from get_slots response
- `stringify_slotlet`: true if slotlet values must be strings (converts integers to strings)
- `error_hms_booking_ids`: array of booking_id values that indicate failure (e.g. `["0"]`)
- `gender_mapping`: `{"male": "<HIS_value>", "female": "<HIS_value>", "others": "<HIS_value>"}` — maps Qikwell gender to hospital's expected values. When patient gender is blank, the value from `default_params.patient_gender` is used as input to this mapping. Always include an `"others"` key for the fallback. Example: if hospital wants actual gender with "Other" as fallback → `{"male": "Male", "female": "Female", "others": "Other"}` + `default_params.patient_gender: "others"`. If hospital wants a fixed value regardless → map all keys to the same value.
- `dob_format`: strftime format for DOB (e.g. `"%Y-%m-%d"`, `"%d-%m-%Y"`). The `default_params.patient_dob` string is parsed via `Date.parse` before formatting, so any standard date string works as the default.
- `require_create_patient`: true to call register_patient API before booking
- `require_doctor_info`: true to include `doctor_name` and `doctor_speciality` in request data

**reschedule_appointment:**
- `require_doctor_slotlet`: true (same as create)
- `stringify_slotlet`: true (same as create)
- `error_hms_booking_ids`: array (same as create)

**appointment_status:**
- `response_status`: mapping of hospital status values to Qikwell states. The Qikwell state values must match these EXACT strings (case-insensitive comparison): `"Cancelled"`, `"Checked In"` (or `"Checked_in"`), `"Checked Out"` (or `"Checked_out"`), `"Rescheduled"`, `"No Show"` (or `"No_show"`). Example: `{"Done": "Checked Out", "Cancel": "Cancelled", "Arrived": "Checked In", "NoShow": "No Show"}`

**bulk_appointments_status:**
- `response_status`: same as appointment_status
- `is_based_on_appointment_ids`: true if the API accepts a list of booking IDs to filter by

**fetch_uhid:**
- `lookup_by`: `"phone_number"` | `"booking_id"` | `"date"` | `"date_range"`
- `invalid_uhid_values`: array of UHID values to ignore (e.g. `["--", "0"]`)
- `start_date` / `end_date`: ISO date strings to limit which appointments are included (e.g. `"2024-01-01"`)
- `date_range_from` / `date_range_to`: Override min/max date for date_range lookup (otherwise computed from appointment dates)

**hold_appointment_slot:**
- Same structure as create_appointment, used when hospital requires a 2-step booking (hold then confirm)
- `require_doctor_slotlet`: true if hold needs slot data

**fetch_followup_apts:**
- `start_date` / `end_date`: Required ISO date strings defining the appointment date range to scan for UHIDs

### target_structure Generation Rules

The `target_structure` is a YAML string representing a serialized Ruby Node tree. It maps the hospital's response fields to standardized Qikwell field names.

**Node types:**
- `Hash` — object/dict; `data: {}`, children are key-value pairs. Optional `value` field to unwrap nested response (e.g. `value: source["data"]`)
- `Array` — list; `data: []`, EXACTLY ONE child defines element structure, `value` is the eval path to the source array (e.g. `source["data"]["timeslots"]` or just `source`)
- `String` — leaf node; `data: ''`, `value` is the hospital's response field name, `name` is the Qikwell standard field name

**String node advanced features:**
- `delimiter`: If set, splits the string value by this delimiter (e.g. `delimiter: ","` turns `"a,b,c"` into `["a","b","c"]`)
- `index`: If set (after delimiter split), picks the element at this index from the resulting array
- `value` can also reference a `Proc` in code (not in config), but for configs always use a field name string

**Required output fields per operation:**

| Operation | Standard output fields |
|---|---|
| get_slots | `slots[]` → `start_time`, plus optionally: `session_id`, `slot_number`, `id`, `duration`, `end_time`, `is_available`, `slotTime`, `status` |
| create_appointment | `booking_id`, `status`, `message`, optionally `uhid`, `visit_id` |
| cancel_appointment | `reason` or `message` |
| reschedule_appointment | `booking_id`, `message` |
| appointment_status | `appointment_status`, optionally `uhid`, `appointment_date`, `appointment_time` |
| bulk_appointments_status | `appointments[]` → `booking_id`, `appointment_status`, optionally `uhid` |
| fetch_uhid | Either `uhid` (single) OR `uhid_list[]` → `booking_id`, `uhid` (bulk) |
| hold_appointment_slot | `booking_id`, `message` |
| register_patient | `uhid` |
| fetch_followup_apts | Array or single: `hms_booking_id`, `appointment_date`, `status`, optionally `hms_doctor_code`, `hms_clinic_code`, `source` |
| validate_mobile | `patient_list[]` → `patient_name`, `patient_mobile`, `patient_uhid`, `patient_gender`, `patient_age`, `patient_email`, `patient_dob`, `patient_salutation`, optionally `patient_first_name`, `patient_last_name` |

**Template for a flat Hash response** (e.g. create_appointment):
```yaml
--- !ruby/object:Node
type: Hash
children:
- !ruby/object:Node
  type: String
  children: []
  value: <hospital_field_name>
  name: booking_id
  data: ''
  delimiter:
  index:
- !ruby/object:Node
  type: String
  children: []
  value: <hospital_field_name>
  name: status
  data: ''
  delimiter:
  index:
- !ruby/object:Node
  type: String
  children: []
  value: <hospital_field_name>
  name: message
  data: ''
  delimiter:
  index:
value:
name:
data: {}
delimiter:
index:
```

**Template for an Array-in-Hash response** (e.g. get_slots):
```yaml
--- !ruby/object:Node
type: Hash
children:
- !ruby/object:Node
  type: Array
  children:
  - !ruby/object:Node
    type: Hash
    children:
    - !ruby/object:Node
      type: String
      children: []
      value: <hospital_time_field>
      name: start_time
      data: ''
      delimiter:
      index:
    value:
    name:
    data: {}
    delimiter:
    index:
  value: source["<hospital_array_key>"]
  name: slots
  data: []
  delimiter:
  index:
value:
name:
data: {}
delimiter:
index:
```

If the response IS the array directly (no wrapper key), use `value: source` on the Array node.

If the response needs nested access, chain it: `value: source["data"]["appointments"]`

You can also use `||` to concatenate multiple arrays: `value: source["morning"]||source["evening"]`

**Template for nested root** (response body is wrapped, e.g. `{"data": {"uhid": "123"}}`):
Set the root Hash node's `value: source["data"]` to unwrap before traversing children.

**Template for unwrapping to first element** (response is array, need first item's fields):
Set the root Hash node's `value: source[0]` to access the first element.

### What gets persisted from API responses

After a successful API call, the framework automatically persists certain fields from the parsed response as `AppointmentAttributes`. **You must include these fields in your `target_structure` if the hospital's response contains them, otherwise they are lost.**

**From `create_appointment` response** (`create_apt` method):

| Parsed field | Stored as `AppointmentAttribute` | Condition |
|---|---|---|
| `booking_id` | `hms_booking_id` | Always (required for status polling, cancel, reschedule) |
| `uhid` | `hms_uhid` | When present (via `persist_uhid`) |
| `visit_id` | `visit_id` | When present |
| `session_id` | `session_id` | When slotlet is present |
| `slot_number` | `slot_number` | When slotlet is present |

**From `appointment_status` response** (`process_sync_appointment_status`):

| Parsed field | Stored as | Condition |
|---|---|---|
| `appointment_status` | Triggers status change (Checked In, Checked Out, Cancelled, etc.) | Mapped via `response_status` |
| `uhid` | `hms_uhid` | When present (via `persist_uhid`) |

**From `hold_appointment_slot` response**: `booking_id` is passed to `create_appointment` as the `booking_id` request_data field.

**If the hospital response contains useful fields not in this list** (e.g. `visitId`, `thmsPatientId`):
1. Add the field to the `target_structure` with an appropriate `name` — but **ONLY if the attribute name exists in the table below**
2. If it exists, add a persistence line in `create_apt`: `AppointmentAttributes.update_attribute(appointment.id, '<attr_name>', response['<field_name>'].to_s) if response&.dig('<field_name>').present?`
3. **CRITICAL**: If the attribute name does NOT exist in the table below, do NOT add it to the `target_structure` or persistence code. Instead, note it in the PR description as a field that could be stored if a new attribute is created. Extracting to a non-existent attribute name will silently fail or error at runtime.

**Valid `appointment_attributes` names (from DB):**
```
id  | name
----|----------------------------------
 85 | hms_booking_id
103 | hms_uhid
 11 | visit_id
 98 | session_id
 99 | slot_number
102 | apt_start_time
100 | previous_appointment_start_time
101 | previous_session_id
  5 | cancel_time
  9 | category
 73 | dob
 71 | gender
 72 | occupation
 67 | ADDRESS
 78 | address_line1
 79 | address_line2
 80 | pincode
 81 | additional_mobile_number
 69 | FEE
 65 | reference_id
 31 | transaction_id
 32 | transaction_status
 86 | prepaid_refunded
 88 | payment_source
 93 | payment_released
 66 | is_prime
 95 | appointment_tag
 89 | apt_visit_type
 90 | remote_apt_id
 91 | fabric_id
 92 | DAS_ID
 96 | follow_up_done
 97 | in_follow_up
 82 | Doctor_Name
 83 | Specialty
 84 | Walk_in_date_and_time
 87 | FORTIS_TIME
 68 | WAITTIME
 70 | demo_field
  1 | widget_name
  2 | FORTIS_ID
  3 | FORTIS_CONFIRM_CODE
  4 | ipd_appointment
  6 | report_handover_info
  7 | report_handover_time
 10 | AKHIL_ID
 75 | CAREFIT_BOOK_ID
 76 | VIRINCHI_BOOK_ID
 77 | SUNSHINE_BOOK_ID
 74 | COUPON_FULFILLMENT
```
**Commonly used for integrations:** `hms_booking_id` (85), `hms_uhid` (103), `visit_id` (11), `session_id` (98), `slot_number` (99), `apt_start_time` (102), `reference_id` (65), `remote_apt_id` (90)

---

## STEP 4: Validate the Config

Run these checks on your generated config:

1. **request_params validity**: Every value in `request_params` must be from the VALID data sources listed above for that operation. If a hospital param doesn't map to any Qikwell source, move it to `static_request_params`.

2. **target_structure correctness**:
   - Root node must be `type: Hash`
   - Array nodes must have exactly one child (the element template)
   - String leaf nodes: `value` = hospital's actual response field name, `name` = Qikwell standard field
   - Array node `value` must be a valid Ruby eval path (e.g. `source["key"]`)
   - All required output fields for each operation must be present
   - The YAML must be valid and deserializable as `!ruby/object:Node`
   - `delimiter` and `index` should be `~` (YAML null) unless actively used

3. **Cross-operation consistency**:
   - If `create_appointment` has `require_doctor_slotlet: true`, then `get_slots` target_structure must output the fields that create uses (e.g. `session_id`, `slot_number`)
   - If `reschedule_appointment.required` is true and it uses `previous_session_id`, then `create_appointment` must store `session_id` (it's auto-stored by the framework when slotlet is present)
   - `error_hms_booking_ids` values must match the hospital's actual error indicators
   - If `hold_appointment_slot` is required, both `create_appointment` and `reschedule_appointment` will call it first and pass `booking_id`/`next_booking_id`

4. **URL/params consistency**:
   - GET requests with `%s` in request_url must have matching `url_params` array (one entry per `%s`)
   - POST requests should have `request_params` (not url_params), though they CAN have both
   - `request_headers` must match what the API doc requires
   - Auth tokens/API keys that are clinic-specific should NOT be hardcoded — note them as dynamic_fields

5. **Auth consistency**:
   - If `dynamic_auth_token.immediate_sync` is true, APIs using `dynamic_appointment_token` will trigger an inline token fetch
   - If `dynamic_auth_token.enable_token_caching` is true, ensure `expiry_time` is set appropriately
   - `auth_token_template` must contain a `{...}` placeholder for the token value

6. **Dry run — trace the request payload for each required API**:

   For each API where `required: true`, simulate the full data flow using a sample patient and verify the final HTTP payload matches the hospital's expected format.

   **How to dry run `create_appointment`:**
   1. Start with sample patient data as it would come from `get_appointment_patient_details`:
      ```
      patient_name: "Amit Kumar", patient_mobile: "9999999999", patient_email: "",
      patient_dob: <from patient.dob or default_params>, patient_gender: <from patient.gender or default_params>,
      appointment_qikwell_id: 12345
      ```
   2. Trace through each field in `request_params`:
      - For each leaf value, check: is it a key in `request_data`? → resolved. Not a key? → kept as literal string.
      - For nested Hashes, recurse the same way.
   3. Apply `static_request_params` via `deep_merge!` on top.
   4. **Check the final payload against the hospital's sample request from the API doc.** Every field must match in structure, nesting, and value type.
   5. **Test edge cases explicitly:**
      - Patient with **no email** → does `default_params.patient_email` kick in? Show the resolved value.
      - Patient with **no gender** → does `default_params.patient_gender` + `gender_mapping` produce the correct value?
      - Patient with **no DOB** → does `default_params.patient_dob` get parsed and formatted with `dob_format`? Show the resolved value.
      - Patient with **all data present** → does the real data flow through (not the defaults)?

   **How to dry run slot availability (CRITICAL for Pattern B/C):**

   When `get_slots.required: false`, Practo is the source of truth for slots. Verify that patients will still see availability by tracing **all 4 code paths** that can affect slot visibility. For each path, read the actual code, trace the execution with the config values, and confirm whether BlockSlots are created/modified.

   **Path 1: Poller cron — `sync_available_doctor_slots`**
   This runs periodically to sync HIS slots into Practo's BlockSlot table.
   1. Read the method. Find where it loads `request_builder = clinic_config['apis']['get_slots']`.
   2. With our config (`get_slots: { "required": false }`), trace: does it hit the guard `request_builder.blank? || !request_builder['required']`? If yes → returns `{ method_not_required: true }` before line `PollerServiceUtils.fetch_or_create_exceptions_block_slots(...)`. No BlockSlots created. ✅
   3. If the guard is missing → `fetch_or_create_exceptions_block_slots` creates BlockSlot records with `is_blocked: true` for every 5-min block in the doctor's schedule → then the Lambda is invoked with no real HIS API → callback `process_sync_available_doctor_slots` receives empty response → `toggle_block_slots_status(..., available_times=[], ...)` → `BlockSlot.where(...).update_all(is_blocked: true)` blocks all → `return { message: 'No available times' }` → **patients see zero availability**. ❌

   **Path 2: On-demand sync — `PollerServiceUtils.sync_doctor_slots` → impl `sync_doctor_slots`**
   This is triggered by the **Book system** when a patient opens a doctor's profile page (`ondemand_slot_sync`). This is the most commonly missed guard.
   1. Read `PollerServiceUtils.sync_doctor_slots` in `lib/utils/poller_service_utils.rb`. Confirm it calls `integrate_impl.sync_doctor_slots(clinic, doctor, Date.today, nil, last_updated)`.
   2. Read `sync_doctor_slots` in the impl. Find where it loads `api_builder = clinic_config['apis']['get_slots']`.
   3. With our config, trace: does it hit `api_builder.blank? || !api_builder['required']`? If yes → returns early before `fetch_or_create_exceptions_block_slots`. ✅
   4. If the guard is missing → the method proceeds to:
      - `PollerServiceUtils.fetch_or_create_exceptions_block_slots([slot], date, end_date)` → creates BlockSlot records (all `is_blocked: true`)
      - `fetch_slots(clinic, doctor, date.to_time)` → attempts HIS API call with no configured endpoint → returns empty `available_times = []`
      - `toggle_block_slots_status(clinic.id, doctor.id, [], date, ...)` → `BlockSlot.where(...).update_all(is_blocked: true)` blocks all → `return { message: 'No available times' }` since `available_times` is empty
      - **Result: Patient opens profile → all slots blocked → sees zero availability** ❌

   **Path 3: Post-booking sync — `create_apt` → `sync_doctor_slots`**
   After a successful booking, `create_apt` calls `sync_doctor_slots(clinic, appointment.doctor, appointment.display_aptdatetime.to_date, 0)`.
   1. Trace: this calls the same `sync_doctor_slots` method verified in Path 2.
   2. With the guard in place → returns `{ method_not_required: true }`. Remaining Practo slots stay visible. ✅
   3. Without the guard → blocks all remaining slots for the day after a single booking. ❌

   **Path 4: Callback — `process_sync_available_doctor_slots`**
   This is the async callback after the poller Lambda returns slot data.
   1. Since Path 1 returns early, the Lambda is never invoked, so this callback is **never triggered**. ✅
   2. However, verify defensively: if it were called with empty response data, trace what happens:
      - `info['response'] = { 'slots' => [] }` (empty response fallback)
      - `available_times = []` after parsing
      - `toggle_block_slots_status(clinic.id, doctor_id, [], date, ...)` → blocks all slots ❌
      - This path is unreachable with the guard, but documents why the guard matters.

   **Produce a summary table:**
   | Code Path | Guard Location | Blocks Slots? | Status |
   |-----------|---------------|---------------|--------|
   | Poller cron (`sync_available_doctor_slots`) | Early return check | No — returns early | ✅ SAFE |
   | On-demand (`sync_doctor_slots`) | Early return check | No — returns early | ✅ SAFE |
   | Post-booking (`create_apt` → `sync_doctor_slots`) | Same as above | No — returns early | ✅ SAFE |
   | Callback (`process_sync_available_doctor_slots`) | Never invoked | N/A | ✅ SAFE |

   **End-to-end scenario — trace a complete patient booking:**
   1. Patient opens doctor profile on Practo → Book system calls `PollerServiceUtils.sync_doctor_slots(doctor_id, clinic_id)` → impl `sync_doctor_slots` returns `{ method_not_required: true }` → Practo's native slot schedule is shown (no BlockSlots interfere) → patient sees available slots (e.g. 9:00, 9:15, 10:00, 10:30...)
   2. Patient books 10:30 AM → `create_apt` fires → `require_doctor_slotlet: false` → `get_doctor_slotlet` NOT called → `slotlet = nil` → `start_date_obj = appointment.display_aptdatetime` (10:30 AM) → `date_fields['date'] = start_date_obj.strftime(request_date_format)` → payload sent to hospital with the Practo booking time directly
   3. Booking succeeds → `sync_doctor_slots` called at end of `create_apt` → returns early (guard) → remaining Practo slots stay visible
   4. Status polling picks up the appointment → UHID persisted from response

   **If any path fails (guard missing or BlockSlots created), the fix MUST be applied in STEP 5a before proceeding.**

   **How to dry run `appointment_status`:**
   1. Start with: `hms_booking_id: "abc-123"`, `appointment_qikwell_id: 12345`, date fields
   2. If using `url_params` with `%s`, substitute and show the final URL
   3. Apply `response_status` mapping to the hospital's sample response — verify the mapped Qikwell status is correct

   **How to dry run any other required API:** Same approach — build `request_data`, resolve `request_params`, show the final payload, compare against the API doc's expected format.

   **If any dry-run value is wrong, fix the config before proceeding.**

7. **Output any validation errors with specific fixes.**

---

## STEP 4.5: Explain the Config

After validation, produce a detailed walkthrough of the generated config. This serves as a review artifact — it lets the reader understand every decision without cross-referencing the API doc. Walk through the config section by section:

### 1. Integration Pattern Summary
State which patterns (A–F) apply and why. Explain the practical implication:
- Which system owns slots? (Practo vs HIS)
- Which APIs are push vs poll?
- What's the appointment lifecycle? (create → status poll → UHID)

### 2. Top-level Fields
For each top-level field (`integration_name`, `sync_appointment`, `days_per_payload`, `is_get_slots_by_range_enabled`, `terminal_statuses`, etc.), explain:
- What the value is and why it was chosen
- What would change if a different value were used

### 3. Per-API Walkthrough
For each API in the `apis` block (both required and not-required), explain:

**If `required: true`:**
- **Purpose**: What this API does in the integration lifecycle
- **Request flow**: HTTP method + URL → how `request_params` maps hospital field names to Qikwell data sources → what `static_request_params` adds on top → final payload structure
- **URL construction**: If `url_params` are used, show how `%s` substitution works with a concrete example
- **Date handling**: Which `request_date_format` is used, what the `date` field resolves to (e.g. `"2026-04-15T10:30:00+05:30"`), and which date source is used (`appointment.display_aptdatetime` vs `Date.today`)
- **Response parsing**: Walk through the `target_structure` node tree — which hospital response fields map to which Qikwell standard names, and what `value: source["data"]` does (unwraps the response)
- **Persisted fields**: Which fields from the response are stored as `AppointmentAttributes` (e.g. `booking_id` → `hms_booking_id`, `uhid` → `hms_uhid`, `visit_id` → `visit_id`)
- **Status mapping** (for `appointment_status`): Show the `response_status` table — hospital value → Qikwell state → what action is triggered (cancel, check-in, check-out, no-show)
- **Special flags**: Explain any operation-specific flags like `require_doctor_slotlet`, `require_doctor_info`, `gender_mapping`, `dob_format`, `error_hms_booking_ids`, etc.

**If `required: false`:**
- **Why it's disabled**: What the hospital doesn't provide (e.g. "no cancel API — Practo handles cancellation locally") or what's handled another way (e.g. "UHID comes from status polling, not a separate fetch_uhid API")

### 4. Default Params & Fallback Logic
For each field in `default_params`, explain the complete resolution chain:
- **`patient_email`**: `appointment.patient_email` → if blank → `default_params.patient_email` → final value (e.g. `"true@example.com"`)
- **`patient_gender`**: `patient.gender` → if blank → `default_params.patient_gender` (e.g. `"others"`) → fed into `gender_mapping["others"]` → final value (e.g. `"Other"`)
- **`patient_dob`**: `patient.dob` → if blank → `Date.parse(default_params.patient_dob)` → `.strftime(dob_format)` → final value (e.g. `"1987-01-01"`)

For each field in `static_request_params`, explain:
- Which hospital fields these are and why they're constants (e.g. `"reason": "Other"` — API doc says "Constant")

### 5. Auth & Headers
- What authentication mechanism is used (static API key, OAuth, none)
- Which headers are sent and which need per-clinic configuration
- If `dynamic_auth_token` is used: token fetch flow, caching, expiry
- Flag any placeholder values (e.g. `"<provided_by_hospital>"`) that must be replaced before go-live

### 6. Nested Request Body (if applicable)
If `request_params` uses nested Hashes (Pattern F), show:
- The full resolved payload with sample data (not just the template)
- How `GenericParser.generate_payload` recurses into nested structures
- How `static_request_params` is deep-merged on top (e.g. `appointment.reason`, `appointment.notes` are merged into the `appointment` object that already has `scheduledAt`, `doctorName`)

---

## STEP 5: Assess Code Changes

**IMPORTANT: You MUST read the actual code before deciding "no changes needed." Do not assume the framework handles everything correctly. Verify by reading the methods.**

### 5a. Slot sync safety check (CRITICAL for Pattern B/C integrations)

**If `get_slots.required: false` (Practo manages slots, no HIS slot API), you MUST verify that ALL slot sync methods respect this flag.** If they don't, the poller will block all Practo slots and patients won't be able to book.

**Do this verification:**

1. Read the `sync_available_doctor_slots` method in `lib/integrate/implementations/qikwell_generic_shadow_impl.rb`. Verify it checks `request_builder.blank? || !request_builder['required']` and returns early. (This one typically already has the guard.)

2. **Read the `sync_doctor_slots` method in the same file.** This is called by the Book system's `ondemand_slot_sync` (triggered when a patient views a doctor's profile). Check whether it also has a guard for `get_slots.required: false`. 
   - If it does NOT check `api_builder['required']` before proceeding, it will:
     - Call `fetch_or_create_exceptions_block_slots` → creates BlockSlot records (all blocked)
     - Call `fetch_slots` → returns empty (no HIS API)
     - Call `toggle_block_slots_status` with empty array → ALL slots stay blocked
     - **Result: Patients see zero availability even though Practo slots exist**
   - **FIX**: Add the same guard that `sync_available_doctor_slots` has: `return { method_not_required: true } if api_builder.blank? || !api_builder['required']` right after `api_builder = clinic_config['apis']['get_slots']` and before any slot manipulation logic.

3. Also check `fetch_slots` and `fetch_bulk_slots` methods — these are called by `sync_doctor_slots` and `create_apt` (when `require_doctor_slotlet: true`). They should be safe if `sync_doctor_slots` returns early, but verify.

4. Check `PollerServiceUtils.sync_doctor_slots` in `lib/utils/poller_service_utils.rb` — this is the entry point from the Book system. Verify it calls the impl's `sync_doctor_slots` which you just verified/fixed.

### 5b. Data source gaps — verify by reading the code

**For each API operation the hospital requires, read the actual method that builds `request_data` and verify that every field the hospital API needs is available.**

Do this:

1. **For `appointment_status`**: Read the `sync_appointment_status` method. Look at the `request_data` hash. Check if it includes all the keys the hospital's status API needs. Common gap: if the hospital uses Practo's appointment ID (e.g. `practoAppointmentId`) instead of `hms_booking_id` for status lookup, check if `appointment_qikwell_id` is in `request_data`. If not, add `'appointment_qikwell_id' => appointment.id` to the hash.

2. **For `create_appointment`**: Read `create_apt`. The patient data comes from `get_appointment_patient_details` — read that method to see all available keys. Slotlet data (start_time, session_id, etc.) is merged only when `require_doctor_slotlet: true`.

3. **For `cancel_appointment`**: Read `cancel_apt`. Check the `request_data` hash. It has `hms_booking_id`, `hms_clinic_code`, `hms_doctor_code`, `hms_department_code`, `reason`, `session_id`, `current_time`, plus patient fields. If the hospital needs additional fields, add them.

4. **For any operation**: If a required field is missing from `request_data`, add `'field_name' => source_value` to the `request_data` hash in that method. This is a safe, backward-compatible change — existing integrations simply won't reference the new key.

### 5c. When a subclass IS needed

Create a new impl file extending `QikwellGenericShadowImpl` ONLY for:
- Multi-step auth with custom hashing (e.g. Manipal's MD5 checkcode)
- SOAP/HL7 FHIR protocols
- Chained API calls (e.g. call API-A, use its response in API-B)
- Custom slot parsing for non-standard bulk response formats
- Any logic that can't be expressed purely in config

Steps:
1. Create `lib/integrate/implementations/qikwell_<hospital_name>_impl.rb` extending `QikwellGenericShadowImpl`
2. Update factory routing in `lib/integrate/factory/integrate_factory.rb` (add a new `when` clause)
3. Override ONLY the methods that need custom behavior
4. Example: `QikwellManipalImpl` overrides `add_additional_auth_data` for MD5 checkcode generation

### 5d. Security check for API keys

If the hospital API uses static API keys in headers:
- **DO NOT hardcode production keys** in the config JSON committed to the repo
- Use a placeholder value and note in the PR that the real key must be configured per clinic via the admin panel
- If the key is already provided in the API doc for testing, use it in the config but flag it in the PR description

---

## STEP 6: Write Output & Create PR

1. **Always create a fresh branch** from `master`:
   - Branch name: `<hospital_name_kebab_case>-integration-v<N>` (e.g. `true-hospitals-integration-v1`)
   - To pick `N`: run `git branch --list '*<hospital_name>*'` and increment from the highest existing version (or start at `v1` if none exist)
   - Run: `git checkout master && git pull origin master && git checkout -b <branch_name>`
   - **Never switch to an existing branch** — old branches may have stale or conflicting work. Always start fresh from `master`.
2. **Check for existing config first**: Before writing, search `lib/integration_agent/configs/` for any existing config file for this hospital. If one exists, **update it in place** rather than creating a new file with a different name. This prevents duplicate configs for the same hospital.
3. Write the validated config to `lib/integration_agent/configs/<hospital_name_snake_case>_config.json`
4. If code changes were needed, write those files
5. Commit, push, and create a PR:
   - `git add` the specific files changed (config + any code changes)
   - Commit with a descriptive message (e.g. "Add TrueHospitals integration config and code changes")
   - `git push -u origin <branch_name>`
   - Create PR via `gh pr create` with:
     - Title: "Add <HospitalName> integration config"
     - Body: Summary of APIs supported, any limitations, and setup instructions (which dynamic_fields to configure per clinic)

---

## INPUT

[Paste or upload the hospital API documentation here]
