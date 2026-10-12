"""Tests for storage repositories."""
import pytest

from hh_applicant_tool.storage.facade import StorageFacade
from hh_applicant_tool.storage.models.vacancy import VacancyModel
from hh_applicant_tool.storage.models.employer import EmployerModel
from hh_applicant_tool.storage.models.resume import ResumeModel


class TestVacanciesRepository:
    """Test vacancy storage operations."""

    def test_save_single_vacancy(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Save a single vacancy to database."""
        storage.vacancies.save(sample_vacancy_data)
        assert storage.vacancies.count_total() == 1

    def test_upsert_vacancy_by_id(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Upsert should update existing vacancy, not create duplicate."""
        # Save first time
        storage.vacancies.save(sample_vacancy_data)
        assert storage.vacancies.count_total() == 1

        # Save with modified data (same ID)
        modified = sample_vacancy_data.copy()
        modified["name"] = "Senior Python Developer"
        storage.vacancies.save(modified)

        # Should still have only 1 vacancy
        assert storage.vacancies.count_total() == 1

        # Data should be updated
        vacancy = storage.vacancies.get(sample_vacancy_data["id"])
        assert vacancy.name == "Senior Python Developer"

    def test_save_batch_vacancies(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Save multiple vacancies in batch."""
        vacancies = [
            sample_vacancy_data,
            {**sample_vacancy_data, "id": 12346, "name": "Backend Developer"},
            {**sample_vacancy_data, "id": 12347, "name": "Frontend Developer"},
        ]

        storage.vacancies.save_batch(vacancies)
        assert storage.vacancies.count_total() == 3

    def test_find_vacancy_by_id(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Find vacancy by ID."""
        storage.vacancies.save(sample_vacancy_data)

        vacancy = storage.vacancies.get(sample_vacancy_data["id"])
        assert vacancy is not None
        assert vacancy.name == "Python Developer"
        assert vacancy.salary_from == 100000

    def test_find_nonexistent_vacancy_returns_none(
        self, storage: StorageFacade
    ):
        """Getting nonexistent vacancy returns None."""
        vacancy = storage.vacancies.get(999999)
        assert vacancy is None

    def test_delete_vacancy(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Delete vacancy from database."""
        storage.vacancies.save(sample_vacancy_data)
        assert storage.vacancies.count_total() == 1

        storage.vacancies.delete(sample_vacancy_data["id"])
        assert storage.vacancies.count_total() == 0

    def test_find_with_filter_salary_from(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Find vacancies filtered by salary_from."""
        storage.vacancies.save(sample_vacancy_data)
        storage.vacancies.save({
            **sample_vacancy_data,
            "id": 12346,
            "salary": {"from": 50000, "to": 80000, "currency": "RUR"},
        })

        # Find vacancies with salary >= 100000
        results = list(storage.vacancies.find(salary_from__ge=100000))
        assert len(results) == 1
        assert results[0].salary_from >= 100000

    def test_vacancy_salary_normalization(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Test salary normalization (from/to can't be both 0)."""
        data = {
            **sample_vacancy_data,
            "salary": {"from": None, "to": None, "currency": "RUR"},
        }
        storage.vacancies.save(data)
        vacancy = storage.vacancies.get(sample_vacancy_data["id"])

        # Both should be 0 after normalization
        assert vacancy.salary_from == 0
        assert vacancy.salary_to == 0

    def test_vacancy_with_partial_salary(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Test vacancy with only 'to' salary specified."""
        data = {
            **sample_vacancy_data,
            "salary": {"from": None, "to": 200000, "currency": "RUR"},
        }
        storage.vacancies.save(data)
        vacancy = storage.vacancies.get(sample_vacancy_data["id"])

        # Should use 'to' for both when 'from' is None
        assert vacancy.salary_from == 200000
        assert vacancy.salary_to == 200000


class TestEmployersRepository:
    """Test employer storage operations."""

    def test_save_employer(
        self, storage: StorageFacade, sample_employer_data: dict
    ):
        """Save employer to database."""
        storage.employers.save(sample_employer_data)
        assert storage.employers.count_total() == 1

    def test_get_employer(
        self, storage: StorageFacade, sample_employer_data: dict
    ):
        """Get employer by ID."""
        storage.employers.save(sample_employer_data)

        employer = storage.employers.get(sample_employer_data["id"])
        assert employer is not None
        assert employer.name == "Tech Corp"

    def test_upsert_employer(
        self, storage: StorageFacade, sample_employer_data: dict
    ):
        """Update employer should not create duplicate."""
        storage.employers.save(sample_employer_data)

        modified = sample_employer_data.copy()
        modified["name"] = "Tech Corp Updated"
        storage.employers.save(modified)

        assert storage.employers.count_total() == 1
        employer = storage.employers.get(sample_employer_data["id"])
        assert employer.name == "Tech Corp Updated"


class TestResumesRepository:
    """Test resume storage operations."""

    def test_partial_resume_does_not_erase_known_title(
        self, storage: StorageFacade, sample_resume_data: dict
    ):
        storage.resumes.save(sample_resume_data)
        partial = {**sample_resume_data, "title": None, "status": {"id": "blocked"}}
        storage.resumes.save(partial)
        saved = storage.resumes.get(sample_resume_data["id"])
        assert saved.title == sample_resume_data["title"]
        assert saved.status_id == "blocked"
        assert partial["title"] is None

    def test_titleless_resume_does_not_abort_other_resumes_in_batch(
        self, storage: StorageFacade, sample_resume_data: dict
    ):
        storage.resumes.save_batch([
            {"id": "removed-resume", "status": {"id": "blocked"}},
            sample_resume_data,
        ])
        assert storage.resumes.count_total() == 2
        assert storage.resumes.get(sample_resume_data["id"]).title == sample_resume_data["title"]
        assert storage.resumes.get("removed-resume").title == "Резюме без названия"

    def test_save_resume(
        self, storage: StorageFacade, sample_resume_data: dict
    ):
        """Save resume to database."""
        storage.resumes.save(sample_resume_data)
        assert storage.resumes.count_total() == 1

    def test_get_resume(
        self, storage: StorageFacade, sample_resume_data: dict
    ):
        """Get resume by ID."""
        storage.resumes.save(sample_resume_data)

        resume = storage.resumes.get(sample_resume_data["id"])
        assert resume is not None
        assert resume.title == "Senior Python Developer"

    def test_save_multiple_resumes(
        self, storage: StorageFacade, sample_resume_data: dict
    ):
        """Save multiple resumes."""
        resumes = [
            sample_resume_data,
            {
                "id": "resume_124",
                "title": "DevOps Engineer",
                "status": {"id": "published"},
            },
        ]

        storage.resumes.save_batch(resumes)
        assert storage.resumes.count_total() == 2


class TestDatabaseTransactions:
    """Test transaction handling."""

    def test_context_manager_commit(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Context manager should commit successfully."""
        vacancy_repo = storage.vacancies
        with vacancy_repo:
            vacancy_repo.save(sample_vacancy_data, commit=False)

        # Should be committed after exiting context
        assert storage.vacancies.count_total() == 1

    def test_context_manager_rollback_on_error(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Context manager should rollback on exception."""
        vacancy_repo = storage.vacancies

        try:
            with vacancy_repo:
                vacancy_repo.save(sample_vacancy_data, commit=False)
                raise ValueError("Test error")
        except ValueError:
            pass

        # Changes should be rolled back
        assert storage.vacancies.count_total() == 0

    def test_multiple_saves_one_batch(
        self, storage: StorageFacade, sample_vacancy_data: dict
    ):
        """Multiple saves in transaction should work."""
        with storage.vacancies:
            storage.vacancies.save(sample_vacancy_data, commit=False)
            storage.vacancies.save(
                {**sample_vacancy_data, "id": 12346},
                commit=False,
            )

        assert storage.vacancies.count_total() == 2
