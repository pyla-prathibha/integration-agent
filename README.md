# Integration Agent

An autonomous Django-based system that uses Claude AI to automatically generate hospital integration configurations for the Qikwell-Dhanvantri platform.

## Overview

The Integration Agent reads hospital API documentation, analyzes the qikwell-dhanvantri codebase, generates validated `generic_config` JSON files, and automatically creates pull requests with the integration code.

**Key Features:**
- ✅ Autonomous config generation from API docs
- ✅ Multi-layer input validation (client, server, agent)
- ✅ Automatic PR creation with generated configs
- ✅ Database tracking of all integration runs
- ✅ Real-time status monitoring
- ✅ Supports 6+ hospital integration patterns

## Architecture

```
┌─────────────────────────────────────────┐
│ Django Admin Interface                  │
│ - Upload hospital API docs              │
│ - View integration status               │
│ - Download generated configs            │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ AgentRun Model (SQLite Database)        │
│ - Tracks runs, status, configs, PRs     │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ Agent Service                           │
│ - Client-side validation                │
│ - Server-side validation                │
│ - Agent-side validation                 │
│ - Claude Agent SDK integration          │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ Claude Agent (with System Prompt)       │
│ - Parses API documentation              │
│ - Reads qikwell-dhanvantri codebase     │
│ - Generates config JSON                 │
│ - Creates git branch & PR               │
└─────────────────────────────────────────┘
```

## Setup

### Requirements
- Python 3.10+
- Django 6.0+
- Claude Agent SDK 0.1.68+
- Git

### Installation

1. **Clone the repository:**
```bash
git clone https://github.com/pyla-pratibha/integration-agent.git
cd integration-agent
```

2. **Create virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Configure environment:**
```bash
cp .env.example .env  # Create your own if needed
```

5. **Run migrations:**
```bash
python manage.py migrate
```

6. **Create superuser:**
```bash
python manage.py createsuperuser
```

7. **Start development server:**
```bash
python manage.py runserver 8000
```

8. **Access admin panel:**
```
http://localhost:8000/admin/
```

## Usage

### Upload Hospital Integration Doc

1. Go to Admin → Agent → Upload Integration Doc
2. Enter hospital name (e.g., "True Hospitals")
3. Upload API documentation file (.md, .pdf, .txt)
4. Optionally upload Postman collection (.json)
5. Click "Process Integration"

### Validation

The system validates at three levels:

#### ✅ Client-Side (Browser)
- File size: ≤ 10MB
- Content length: ≥ 200 characters
- No placeholder text ("test hospitals", "sample doc", etc.)
- Contains API keywords: 'api', 'endpoint', 'request', or 'response'
- Real-time feedback with error messages

#### ✅ Server-Side (Django)
- Same validation as client-side
- Runs before database record is created
- Prevents bad data from reaching agent

#### ✅ Agent-Side (Claude)
- Final validation before API call
- Saves Claude API costs
- Marked as failed if validation fails

### Monitor Status

1. Click on a run to view detailed status
2. See agent response and generated config
3. Download config JSON
4. View PR link when completed

## Project Structure

```
integration-agent/
├── agent/                          # Django app
│   ├── migrations/                 # Database migrations
│   ├── templates/agent/
│   │   ├── upload.html            # Upload form with validation
│   │   └── status.html            # Status page
│   ├── admin.py                   # Admin interface + validation
│   ├── models.py                  # AgentRun model
│   ├── agent_service.py           # Claude integration
│   └── views.py
├── integration_agent/              # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── prompts/
│   └── system_prompt.md           # 48KB system prompt for Claude
├── requirements.txt
├── manage.py
└── db.sqlite3                     # Development database
```

## System Prompt

The agent uses a comprehensive 48KB system prompt (`prompts/system_prompt.md`) that includes:

- 6-step methodology for generating configs
- 6 hospital integration patterns (A-F)
- Complete config schema documentation
- Validation rules and code checks
- Reference configurations
- Data source mappings
- Response parsing rules

This ensures consistent, high-quality config generation across all integrations.

## Integration Patterns Supported

The agent automatically detects and handles:

- **Pattern A:** Full Shadow Integration (slots synced from HIS)
- **Pattern B:** Practo Slots + HIS Push (Practo manages slots)
- **Pattern C:** One-way push only (appointments pushed to HIS)
- **Pattern D:** Status via Practo ID (not HMS booking ID)
- **Pattern E:** UHID from status polling (no separate fetch_uhid)
- **Pattern F:** Nested request body (complex JSON structures)

## Configuration Generated

The agent generates a complete `generic_config.json` with:

- Base URL and authentication (static API keys, OAuth, session tokens)
- 13+ API operations (get_slots, create_appointment, cancel, status, etc.)
- Request/response mapping (JSON, XML, nested structures)
- Date format handling
- Status mapping (hospital status → Qikwell status)
- Default values for missing patient data
- Validation rules and error handling

## Database Model

### AgentRun
```python
- hospital_name: CharField
- status: CharField (pending, processing, completed, failed)
- triggered_by: CharField
- document_content: TextField
- postman_content: TextField
- generated_config: TextField (JSON)
- code_changes: TextField
- agent_response: TextField
- pr_url: URLField
- branch_name: CharField
- error_message: TextField
- created_at: DateTimeField
- updated_at: DateTimeField
```

## API Reference

### POST /admin/agent/agentrun/upload/
Upload hospital integration document.

**Form Data:**
- `hospital_name` (required): Name of the hospital
- `integration_doc` (required): API documentation file
- `postman_collection` (optional): Postman collection JSON

**Response:**
- Redirects to status page on success
- Shows error on upload form on failure

### GET /admin/agent/agentrun/<id>/status/
View integration run status.

**Response:**
- Hospital name, status, branch name, PR URL
- Generated config JSON
- Agent response / error message

### GET /admin/agent/agentrun/<id>/download/
Download generated config as JSON file.

## Error Handling

**Validation Errors:**
- User sees error with requirements checklist
- Link to upload form to try again
- Database record not created

**Agent Errors:**
- Run marked as 'failed'
- Error message stored with full traceback
- User can see details in status page

**Network Errors:**
- Agent retries up to 30 turns
- Timeout: 30 seconds per request
- Partial results logged

## Development

### Running Tests
```bash
python manage.py test agent
```

### Creating Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### Accessing Database
```bash
python manage.py shell
>>> from agent.models import AgentRun
>>> AgentRun.objects.all()
```

## Deployment

1. Set `DEBUG = False` in `settings.py`
2. Configure `ALLOWED_HOSTS`
3. Set `SECRET_KEY` from environment variable
4. Use production database (PostgreSQL recommended)
5. Configure Redis for token caching (if using dynamic auth)
6. Set up Celery for async tasks (optional, currently uses threading)

## Contributing

1. Create a new branch: `git checkout -b feature/your-feature`
2. Make changes and test locally
3. Commit with clear message: `git commit -m "Add feature description"`
4. Push and create a pull request

## License

MIT License

## Support

For issues or questions:
1. Check the system prompt for validation rules
2. Review generated config for structure
3. Check agent response for Claude's reasoning
4. See error message for specific validation failures
