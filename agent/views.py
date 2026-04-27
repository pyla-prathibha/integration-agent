from django.shortcuts import render
from django.http import FileResponse
from django.conf import settings
import os


def download_template(request):
    """Download the generic HIS integration document template."""
    template_path = os.path.join(settings.BASE_DIR, 'agent', 'static', 'documents', 'HIS_Integration_Document_Template.pdf')

    if os.path.exists(template_path):
        return FileResponse(open(template_path, 'rb'), as_attachment=True, filename='HIS_Integration_Document_Template.pdf')
    else:
        from django.http import Http404
        raise Http404("Template file not found")
