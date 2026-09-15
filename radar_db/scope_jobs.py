from sqlalchemy import or_, select

from .schema import analysis_scope_jobs, annotation_jobs


def scope_condition(scope_id):
    return or_(
        annotation_jobs.c.scope_id == scope_id,
        select(analysis_scope_jobs.c.job_id).where(
            analysis_scope_jobs.c.scope_id == scope_id,
            analysis_scope_jobs.c.job_id == annotation_jobs.c.job_id,
        ).exists(),
    )