import asyncio
import os
import re
import logging
import threading

from claude_agent_sdk import query, ClaudeAgentOptions

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

    # Ensure we're on the master branch and up to date
    try:
        subprocess.run(
            ['git', 'checkout', 'master'],
            cwd=REPO_DIR,
            capture_output=True,
            check=True,
            timeout=30
        )
        subprocess.run(
            ['git', 'pull', 'origin', 'master'],
            cwd=REPO_DIR,
            capture_output=True,
            check=True,
            timeout=30
        )
    except Exception as e:
        logger.warning(f"Could not prepare master branch: {e}")

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

Step 0: VALIDATE the document BEFORE doing anything else
Check if the hospital API documentation contains ALL of these required fields:
  - Hospital Name and Establishment ID
  - Integration Type (Full Shadow / Practo Slots + HIS Push / One-way Push Only)
  - Authentication details (auth type, headers, API key info)
  - Base URL (production endpoint)
  - At least one API specification with: HTTP method, endpoint path, request body sample, response sample
  - Inline comments on request fields (e.g. /Constant, /Mandatory, /Practo field)
  - Status mapping (HIS status → Practo status) if status API exists
  - Terminal statuses (which statuses mean the appointment is done)

If ANY of the above are missing, you MUST:
  1. List all missing fields/sections clearly
  2. Output the line: VALIDATION_FAILED: <comma-separated list of missing sections>
  3. Do NOT create any branch, config file, or PR
  4. STOP here — do not proceed to any further steps

Only proceed to Step 1 if ALL required fields are present.

Step 1: Read reference configs
- Try to Read: lib/integration_agent/configs/rela_config.json (reference for POST APIs)
- Try to Read: lib/integration_agent/configs/sarvodaya_config.json (reference for GET APIs)
- If files don't exist, continue - the system prompt has config examples

Step 2: Read implementation files
- Read: lib/integrate/implementations/qikwell_generic_shadow_impl.rb (focus on create_apt, fetch_uhid, sync_appointment_status methods)
- Read: lib/utils/generic_parser.rb

Step 3: Generate the config
- Create a JSON config for {hospital_name}
- Detect the integration pattern from the document
- Follow the structure from the reference configs
- Map fields from the API doc to config structure

Step 4: Save the config and notes
- Create directories: Run bash: mkdir -p lib/integration_agent/configs lib/integration_agent/notes
- Use Write tool to save config to: lib/integration_agent/configs/{hospital_slug}_config.json
- Use Write tool to save notes to: lib/integration_agent/notes/{hospital_slug}-integration.md
  (The notes should include: integration pattern, API details, setup checklist, troubleshooting tips)

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

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=system_prompt,
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep"],
                permission_mode="bypassPermissions",
                cwd=REPO_DIR,
                max_turns=30,
            ),
        ):
            if hasattr(message, 'content'):
                for block in message.content:
                    if hasattr(block, 'text'):
                        agent_response_parts.append(block.text)
            if hasattr(message, 'result'):
                result_str = str(message.result)
                agent_response_parts.append(result_str)
                # Capture command failures/errors
                if 'error' in result_str.lower() or 'failed' in result_str.lower() or 'exit code' in result_str.lower():
                    logger.error(f"Agent command failed: {result_str}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Agent query failed: {error_msg}")
        agent_response_parts.append(f"AGENT_ERROR: {error_msg}")
        # Try to extract meaningful error info
        if 'Command failed with exit code' in error_msg:
            agent_response_parts.append("BASH_COMMAND_FAILED: The agent tried to run a command that failed. Check the git branch, permissions, or repository state.")
        raise

    full_response = '\n'.join(agent_response_parts)

    # Check if agent flagged validation failure
    if 'VALIDATION_FAILED' in full_response:
        # Extract what's missing
        validation_match = re.search(r'VALIDATION_FAILED:\s*(.+)', full_response)
        missing = validation_match.group(1) if validation_match else 'Required sections missing'
        return {
            'config_json': '',
            'agent_response': full_response,
            'pr_url': '',
            'branch_name': '',
            'error': 'VALIDATION_FAILED',
            'error_message': f'Document is incomplete. Missing: {missing}',
        }

    # Check if agent flagged a command failure
    if 'COMMAND_FAILED' in full_response:
        # Extract the actual error message
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

    # Check for agent errors
    if 'AGENT_ERROR' in full_response:
        agent_error_match = re.search(r'AGENT_ERROR:\s*(.+?)(?:\n|$)', full_response)
        agent_error = agent_error_match.group(1) if agent_error_match else 'Unknown agent error'
        return {
            'config_json': '',
            'agent_response': full_response,
            'pr_url': '',
            'branch_name': '',
            'error': 'AGENT_ERROR',
            'error_message': f'Agent error: {agent_error}',
        }

    # Check if config file was written
    config_path = os.path.join(REPO_DIR, 'lib', 'integration_agent', 'configs', f'{hospital_slug}_config.json')
    config_json = None
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_json = f.read()

    # Extract PR URL from agent output
    pr_url = None
    # Match GitHub PR URLs: https://github.com/{owner}/{repo}/pull/{number}
    for line in full_response.split('\n'):
        urls = re.findall(r'https://github\.com/[\w\-]+/[\w\-]+/pull/\d+', line)
        if urls:
            pr_url = urls[0]
            break

    # Extract branch name
    branch_name = None
    branch_match = re.search(r'[\w-]+-integration(?:-v\d+)?', full_response)
    if branch_match:
        branch_name = branch_match.group(0)

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
