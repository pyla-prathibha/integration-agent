from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django import forms
from django.shortcuts import redirect, get_object_or_404
from django.http import HttpResponse
import io
import pdfplumber

from .models import AgentRun
from .agent_service import run_agent_async


def extract_document_text(file_obj):
    """Extract text from document based on file type."""
    filename = file_obj.name.lower()

    if filename.endswith('.pdf'):
        try:
            # Read PDF file
            pdf_bytes = io.BytesIO(file_obj.read())
            text_parts = []
            with pdfplumber.open(pdf_bytes) as pdf:
                for page in pdf.pages:
                    # Extract tables first (better structure preservation)
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            for row in table:
                                text_parts.append(' | '.join(str(cell).strip() if cell else '' for cell in row))

                    # Extract remaining text
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)

            return '\n'.join(text_parts)
        except Exception as e:
            raise ValueError(f"Failed to extract PDF: {str(e)}")
    else:
        # For .txt, .md, .doc, .docx — try UTF-8 decoding
        try:
            return file_obj.read().decode('utf-8', errors='replace')
        except Exception as e:
            raise ValueError(f"Failed to read file: {str(e)}")


class DocumentValidator:
    """Validates hospital API documentation before processing."""
    MIN_LENGTH = 200
    PLACEHOLDER_KEYWORDS = ['test hospitals', 'placeholder', 'lorem ipsum', 'sample doc']

    @classmethod
    def validate(cls, document_content):
        """
        Validates document content. Returns (is_valid, error_message).
        """
        if not document_content or not document_content.strip():
            return False, "Document content is empty. Please provide actual API documentation."

        content = document_content.strip()

        # Check minimum length
        if len(content) < cls.MIN_LENGTH:
            return False, f"Document is too short ({len(content)}/{cls.MIN_LENGTH} characters). Please provide complete API documentation with endpoints, schemas, and examples."

        # Check for placeholder content
        lower_content = content.lower()
        if len(content) < 300 and any(p in lower_content for p in cls.PLACEHOLDER_KEYWORDS):
            return False, "Document appears to be placeholder text. Please provide actual hospital API documentation."

        return True, None


class AgentRunAdmin(admin.ModelAdmin):
    list_display = ['id', 'hospital_name', 'status', 'triggered_by', 'created_at', 'pr_link']
    list_filter = ['status']
    readonly_fields = ['hospital_name', 'status', 'triggered_by', 'document_content',
                       'postman_content', 'generated_config', 'code_changes',
                       'agent_response', 'pr_url', 'branch_name', 'error_message',
                       'created_at', 'updated_at']

    def pr_link(self, obj):
        if obj.pr_url:
            return f'<a href="{obj.pr_url}" target="_blank">View PR</a>'
        return '-'
    pr_link.allow_tags = True
    pr_link.short_description = 'PR'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('upload/', self.admin_site.admin_view(self.upload_view), name='agent_upload'),
            path('<int:run_id>/status/', self.admin_site.admin_view(self.status_view), name='agent_status'),
            path('<int:run_id>/download/', self.admin_site.admin_view(self.download_view), name='agent_download'),
        ]
        return custom_urls + urls

    def upload_view(self, request):
        if request.method == 'POST':
            hospital_name = request.POST.get('hospital_name', '').strip()
            integration_doc = request.FILES.get('integration_doc')
            postman_collection = request.FILES.get('postman_collection')

            if not hospital_name:
                return TemplateResponse(request, 'agent/upload.html', {
                    **self.admin_site.each_context(request),
                    'error': 'Hospital name is required.',
                })

            if not integration_doc:
                return TemplateResponse(request, 'agent/upload.html', {
                    **self.admin_site.each_context(request),
                    'error': 'Please upload an integration document.',
                })

            try:
                doc_content = extract_document_text(integration_doc)
                postman_content = postman_collection.read().decode('utf-8', errors='replace') if postman_collection else ''
            except ValueError as e:
                return TemplateResponse(request, 'agent/upload.html', {
                    **self.admin_site.each_context(request),
                    'error': f'Error processing file: {str(e)}',
                })

            # Validate document content
            is_valid, error_msg = DocumentValidator.validate(doc_content)
            if not is_valid:
                return TemplateResponse(request, 'agent/upload.html', {
                    **self.admin_site.each_context(request),
                    'error': error_msg,
                })

            run = AgentRun.objects.create(
                hospital_name=hospital_name,
                status='processing',
                document_content=doc_content,
                postman_content=postman_content,
                triggered_by=request.user.username,
            )

            run_agent_async(run.id)

            return redirect('admin:agent_status', run_id=run.id)

        return TemplateResponse(request, 'agent/upload.html', {
            **self.admin_site.each_context(request),
        })

    def status_view(self, request, run_id):
        run = get_object_or_404(AgentRun, id=run_id)
        return TemplateResponse(request, 'agent/status.html', {
            **self.admin_site.each_context(request),
            'run': run,
        })

    def download_view(self, request, run_id):
        run = get_object_or_404(AgentRun, id=run_id)
        if run.generated_config:
            response = HttpResponse(run.generated_config, content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="{run.hospital_name.lower().replace(" ", "_")}_config.json"'
            return response
        return redirect('admin:agent_status', run_id=run.id)


admin.site.register(AgentRun, AgentRunAdmin)

# Customize admin site
admin.site.site_header = 'Integration Agent'
admin.site.site_title = 'Integration Agent'
admin.site.index_title = 'Dashboard'


# Add "Upload" link to the admin index page
def get_app_list(self, request, app_label=None):
    app_list = admin.AdminSite.get_app_list(self, request, app_label)
    # Add upload link to the agent app
    for app in app_list:
        if app['app_label'] == 'agent':
            app['models'].insert(0, {
                'name': 'Upload Integration Doc',
                'object_name': 'Upload',
                'admin_url': '/admin/agent/agentrun/upload/',
                'view_only': True,
                'add_url': None,
            })
    return app_list

admin.site.get_app_list = get_app_list.__get__(admin.site, type(admin.site))
