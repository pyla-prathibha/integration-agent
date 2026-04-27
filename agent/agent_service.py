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

    # Ensure we're on the optimise-fetch-uhid branch
    try:
        subprocess.run(
            ['git', 'checkout', 'optimise-fetch-uhid'],
            cwd=REPO_DIR,
            capture_output=True,
            check=True,
            timeout=30
        )
    except Exception as e:
        logger.warning(f"Could not checkout optimise-fetch-uhid branch: {e}")

    system_prompt = load_system_prompt()
    hospital_slug = hospital_name.lower().replace(' ', '_')

    # Simple prompt — just the doc + what to do
    prompt = f"""You are generating a hospital integration config for {hospital_name}.

## Input Documentation:
{document_content}

## TASK:
1. Read reference configs from: lib/integration_agent/configs/rela_config.json, lib/integration_agent/configs/sarvodaya_config.json
2. Read codebase files: lib/integrate/implementations/qikwell_generic_shadow_impl.rb, lib/utils/generic_parser.rb
3. Understand the integration pattern from documentation: Practo Slots + HIS Push
4. Generate complete generic_config.json for {hospital_name}
5. Write to: lib/integration_agent/configs/{hospital_slug}_config.json
6. Bash commands only:
   - git checkout -b {hospital_slug}-integration
   - git add lib/integration_agent/configs/{hospital_slug}_config.json
   - git commit -m "Add {hospital_name} integration config"
   - git push origin {hospital_slug}-integration
   - gh pr create --title "Add {hospital_name} integration config" --body "Config for {hospital_name}"

Use only: Read, Write, Bash, Glob, Grep tools."""

## HOSPITAL INTEGRATION DOCUMENT

{document_content}
"""
    if postman_content:
        prompt += f"\n\n## POSTMAN COLLECTION\n\n{postman_content}"

    agent_response_parts = []

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
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
            agent_response_parts.append(str(message.result))

    full_response = '\n'.join(agent_response_parts)

    # Check if config file was written
    config_path = os.path.join(REPO_DIR, 'lib', 'integration_agent', 'configs', f'{hospital_slug}_config.json')
    config_json = None
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config_json = f.read()

    # Extract PR URL from agent output
    pr_url = None
    for line in full_response.split('\n'):
        urls = re.findall(r'https://github\.com/[^\s\)]+/pull/\d+', line)
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

            run.status = 'completed'
            run.generated_config = result['config_json']
            run.agent_response = result['agent_response']
            run.pr_url = result['pr_url']
            run.branch_name = result['branch_name']
            run.save()

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            logger.error(f"Agent failed for run #{run_id}: {e}\n{error_detail}")
            print(f"[AGENT ERROR] Run #{run_id}: {e}\n{error_detail}")
            run.status = 'failed'
            run.error_message = f"{type(e).__name__}: {str(e)}\n\n{error_detail}"
            run.save()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
