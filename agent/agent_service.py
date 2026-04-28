import asyncio
import os
import re
import sys
import logging
import threading

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

logger = logging.getLogger(__name__)

REPO_DIR = '/Users/pylapratibha/Practo/qikwell-dhanvantri'
PROMPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prompts', 'system_prompt.md')


def load_system_prompt():
    if os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, 'r') as f:
            return f.read()
    return 'You are a Hospital Integration Engineer for Practo/Qikwell-Dhanvantri.'


async def call_agent(hospital_name, document_content, postman_content):
    """Run the Claude agent against the local qikwell-dhanvantri repo."""
    import subprocess

    # Ensure we're on a clean master branch before the agent starts
    try:
        # Discard any uncommitted changes from previous runs
        subprocess.run(
            ['git', 'checkout', '.'],
            cwd=REPO_DIR,
            capture_output=True,
            timeout=30
        )
        # Remove any untracked files left by previous runs (configs, notes)
        subprocess.run(
            ['git', 'clean', '-fd', 'lib/integration_agent/'],
            cwd=REPO_DIR,
            capture_output=True,
            timeout=30
        )
        # Switch to master
        result = subprocess.run(
            ['git', 'checkout', 'master'],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        # Pull latest
        result = subprocess.run(
            ['git', 'pull', 'origin', 'master'],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        logger.info("Successfully checked out and updated master branch")
    except subprocess.CalledProcessError as e:
        error_detail = e.stderr or e.stdout or str(e)
        logger.error(f"Failed to prepare master branch: {error_detail}")
        raise RuntimeError(f"Cannot start agent: failed to checkout clean master branch. Error: {error_detail}")
    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out while preparing master branch")
        raise RuntimeError("Cannot start agent: git operation timed out. Check network connectivity and repo state.")

    system_prompt = load_system_prompt()
    hospital_slug = hospital_name.lower().replace(' ', '-')

    # Simplified prompt with explicit instructions
    prompt = f"""Generate a generic_config JSON for {hospital_name} hospital integration.

HOSPITAL API DOCUMENTATION:
{document_content}
"""
    if postman_content:
        prompt += f"\n\nPOSTMAN COLLECTION:\n{postman_content}\n"

    prompt += f"""
STEP-BY-STEP INSTRUCTIONS:

Step 0: STRICT VALIDATION — REJECT incomplete documents BEFORE doing anything else

Carefully check the hospital API documentation for ALL of these required fields:

  1. Hospital Name and Establishment ID
  2. Integration Type — must explicitly state one of: "Full Shadow" / "Practo Slots + HIS Push" / "One-way Push Only"
  3. Authentication details — must include: auth type (API Key / OAuth / Bearer Token), header name, and sample key/token value
  4. Base URL — full production endpoint (e.g. https://api.hospital.com/v1), NOT a placeholder like "https://example.com"
  5. At least one complete API specification with ALL of:
     a. HTTP method (GET/POST/PUT/PATCH)
     b. Full endpoint path (e.g. /api/v1/appointments)
     c. Complete request body sample (actual JSON with field names and sample values, NOT just field descriptions)
     d. Complete response body sample (actual JSON showing success response structure)
  6. Inline comments on request fields — at least some fields must be annotated with: /Constant, /Mandatory, /Optional, /Practo field, /we can send dummy, or /send X if not present
  7. Status mapping — a table or list mapping HIS status values to Practo statuses (e.g. "Confirmed" → "confirmed", "Cancelled" → "cancelled")
  8. Terminal statuses — explicit list of which HIS statuses mean the appointment lifecycle is complete (e.g. "Completed", "Cancelled", "No Show")

If ANY of the above are missing, you MUST:
  1. Do NOT create any branch, config file, or PR
  2. List EVERY missing field with a clear explanation of what is expected
  3. Output a structured response in this EXACT format:

VALIDATION_FAILED: <comma-separated list of missing sections>

MISSING FIELDS DETAILS:
- [Field Name]: <what is missing and what you need>
  Expected format: <show the exact format or example of what should be provided>

Example of a valid document section for each missing field:
<provide a short example snippet for each missing field so the user knows exactly what to add>

ACTION REQUIRED:
Please re-upload the document after adding the missing sections listed above.
You can download the HIS Integration Document Template from the upload page for the correct format.

  4. STOP here — do not proceed to any further steps

Only proceed to Step 1 if ALL 8 required fields are present and contain actual data (not placeholders).

Only proceed to Step 1 if ALL required fields are present.

Step 1: Read reference configs
- Try to Read: lib/integration_agent/configs/rela_config.json (reference for POST APIs)
- Try to Read: lib/integration_agent/configs/sarvodaya_config.json (reference for GET APIs)
- If files don't exist, continue - the system prompt has config examples

Step 2: Read implementation files
- Read: lib/integrate/implementations/qikwell_generic_shadow_impl.rb (focus on ALL methods: create_apt, cancel_apt, reschedule_apt, fetch_uhid, sync_appointment_status, sync_bulk_appointments_status, sync_available_doctor_slots, sync_doctor_slots, sync_dynamic_auth_token, fetch_followup_apts, sync_followup_apt_status, hold_appointment_slot, register_patient, create_lead, validate_mobile, fetch_slots, fetch_bulk_slots, get_appointment_patient_details)
- Read: lib/utils/generic_parser.rb

Step 3: Generate the config
- Create a JSON config for {hospital_name}
- Detect the integration pattern from the document
- Follow the structure from the reference configs
- Map fields from the API doc to config structure

Step 3.5: MANDATORY CODE VERIFICATION (DO NOT SKIP)
Think like an integration engineer: "If I deploy this config, what will happen in production?"

For EVERY operation you set as required:false, verify the codebase handles it safely:

A) If get_slots.required is false (Practo owns slots):
   - Read sync_doctor_slots method in qikwell_generic_shadow_impl.rb
   - Search for the guard: "!api_builder['required']" or "api_builder.blank?"
   - If the guard is MISSING, you MUST add it. Without it, when a patient opens a doctor's profile,
     the on-demand sync will create BlockSlots and block ALL Practo slots — patients see zero availability.
   - Also verify sync_available_doctor_slots has the same guard.
   - Output: "SLOT GUARD CHECK: [method_name] — guard [present/MISSING] at line [N]"

B) For appointment_status: read sync_appointment_status method
   - Check if request_data hash has ALL fields the hospital's status API needs
   - If hospital uses Practo appointment ID (not HMS booking ID), verify 'appointment_qikwell_id' is in request_data
   - If missing, add it: 'appointment_qikwell_id' => appointment.id

C) For create_appointment: read create_apt method
   - Verify all request_params data sources exist in the request_data hash
   - Read get_appointment_patient_details to confirm patient field availability

If code changes are needed, make them NOW before saving the config.

Step 4: Save the config and notes
- Create directories: Run bash: mkdir -p lib/integration_agent/configs lib/integration_agent/notes
- Use Write tool to save config to: lib/integration_agent/configs/{hospital_slug}_config.json
- Use Write tool to save notes to: lib/integration_agent/notes/{hospital_slug}-integration.md
  (The notes should include: integration pattern, code verification results, API details, setup checklist)

Step 5: Commit and push
- Run: git checkout -b {hospital_slug}-integration
- Run: git add lib/integration_agent/configs/{hospital_slug}_config.json lib/integration_agent/notes/{hospital_slug}-integration.md
- Run: git commit -m "Add {hospital_name} integration config and notes"
- If any bash command fails (git checkout, git push, etc.), STOP and report the error clearly
- Example error: "git push failed: fatal: could not read Username for 'https://github.com': No such device or address"
- If you see a command failure, output: COMMAND_FAILED: <actual error message from the command>

Step 6: Create PR against master
- Run: gh pr create --base master --title "Add {hospital_name} integration config" --body "Generated config for {hospital_name}"

Only use these tools: Read, Write, Bash, Glob, Grep
"""

    agent_response_parts = []
    turn_count = 0

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="claude-haiku-4-5",
                system_prompt=system_prompt,
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
                permission_mode="bypassPermissions",
                cwd=REPO_DIR,
                max_turns=35,
            ),
        ):
            turn_count += 1
            msg_type = type(message).__name__
            if isinstance(message, ResultMessage):
                actual_turns = message.num_turns
                cost = message.total_cost_usd
                print(f"[AGENT] Done — {actual_turns} actual turns, ${cost:.4f} cost", flush=True, file=sys.stderr)
            elif hasattr(message, 'content'):
                # Count tool uses in this message
                tool_uses = [b for b in message.content if hasattr(b, 'name')]
                if tool_uses:
                    tool_names = [b.name for b in tool_uses]
                    print(f"[AGENT] msg {turn_count} ({msg_type}) — tools: {', '.join(tool_names)}", flush=True, file=sys.stderr)
            if hasattr(message, 'content'):
                for block in message.content:
                    if hasattr(block, 'text'):
                        agent_response_parts.append(block.text)
            if hasattr(message, 'result'):
                result_str = str(message.result)
                agent_response_parts.append(result_str)
                if 'error' in result_str.lower() or 'failed' in result_str.lower():
                    logger.error(f"Agent command failed: {result_str}")
    except Exception as e:
        error_msg = str(e)
        print(f"[AGENT] Failed after {turn_count} messages: {error_msg}", flush=True, file=sys.stderr)
        logger.error(f"Agent query failed after {turn_count} messages: {error_msg}")
        raise
    full_response = '\n'.join(agent_response_parts)

    # Check for validation failure (catch multiple phrasings the agent may use)
    lower_response = full_response.lower()
    is_validation_failure = (
        'VALIDATION_FAILED' in full_response
        or 'incomplete document' in lower_response
        or ('missing fields' in lower_response and 'action required' in lower_response)
    )
    if is_validation_failure:
        validation_match = re.search(r'VALIDATION_FAILED:\s*(.+)', full_response)
        missing = validation_match.group(1) if validation_match else 'Required sections missing'
        detailed_feedback = full_response.strip()
        return {
            'config_json': '',
            'agent_response': full_response,
            'pr_url': '',
            'branch_name': '',
            'error': 'VALIDATION_FAILED',
            'error_message': detailed_feedback,
        }

    # Check for command failure in text
    if 'COMMAND_FAILED' in full_response:
        command_match = re.search(r'COMMAND_FAILED:\s*(.+?)(?:\n|$)', full_response)
        cmd_error = command_match.group(1) if command_match else 'Unknown command error'
        return {
            'config_json': '',
            'agent_response': full_response,
            'pr_url': '',
            'branch_name': '',
            'error': 'COMMAND_FAILED',
            'error_message': f'Git command failed: {cmd_error}',
        }

    # Try to read config from file
    config_path = os.path.join(REPO_DIR, 'lib', 'integration_agent', 'configs', f'{hospital_slug}_config.json')
    config_json = None
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_json = f.read()

    # Extract PR URL from text
    pr_url = None
    for line in full_response.split('\n'):
        urls = re.findall(r'https://github\.com/[\w\-]+/[\w\-]+/pull/\d+', line)
        if urls:
            pr_url = urls[0]
            break

    # Extract branch name from text
    branch_name = None
    branch_match = re.search(r'[\w-]+-integration(?:-v\d+)?', full_response)
    if branch_match:
        branch_name = branch_match.group(0)

    # Fallback: if no config generated and no PR created, something went wrong
    if not config_json and not pr_url:
        return {
            'config_json': '',
            'agent_response': full_response,
            'pr_url': '',
            'branch_name': '',
            'error': 'NO_OUTPUT',
            'error_message': 'Agent completed but did not generate a config or create a PR. Check the agent response for details.',
        }

    return {
        'config_json': config_json or '',
        'agent_response': full_response,
        'pr_url': pr_url or '',
        'branch_name': branch_name or '',
    }


def run_agent_async(run_id):
    """Run the agent in a background thread."""
    def _run():
        from .models import AgentRun
        run = AgentRun.objects.get(id=run_id)

        try:
            result = asyncio.run(call_agent(
                hospital_name=run.hospital_name,
                document_content=run.document_content,
                postman_content=run.postman_content,
            ))

            # Check if agent reported any failure
            error_type = result.get('error')
            if error_type:
                run.status = 'failed'
                run.error_message = result.get('error_message', 'Unknown error')
                run.agent_response = result['agent_response']
                run.save()
                logger.warning(f"Agent run #{run_id} failed ({error_type}): {result.get('error_message')}")
                return

            run.status = 'completed'
            run.generated_config = result['config_json']
            run.agent_response = result['agent_response']
            run.pr_url = result['pr_url']
            run.branch_name = result['branch_name']
            run.save()

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            error_str = str(e)
            logger.error(f"Agent failed for run #{run_id}: {error_str}\n{error_detail}")
            print(f"[AGENT ERROR] Run #{run_id}: {error_str}\n{error_detail}")

            run.status = 'failed'

            # Extract meaningful error message from the exception
            error_message = error_str
            if 'Command failed with exit code' in error_str:
                error_message = "Bash command failed during git operations (e.g., git push, git commit). Check: 1) GitHub credentials 2) Repository permissions 3) Branch existence"
            elif 'timeout' in error_str.lower():
                error_message = "Agent timed out. The operation took too long. Try uploading a simpler document."
            elif 'validation' in error_str.lower():
                error_message = "Document validation failed. Check that all required sections are present."
            else:
                error_message = f"Agent error: {error_str}"

            run.error_message = error_message
            run.agent_response = error_detail  # Store traceback for debugging
            run.save()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
