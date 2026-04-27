from django.db import models


class AgentRun(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    hospital_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    triggered_by = models.CharField(max_length=255, blank=True, default='admin')
    document_content = models.TextField(blank=True)
    postman_content = models.TextField(blank=True)
    generated_config = models.TextField(blank=True)
    code_changes = models.TextField(blank=True)
    agent_response = models.TextField(blank=True)
    pr_url = models.URLField(blank=True)
    branch_name = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.hospital_name} ({self.status})"
