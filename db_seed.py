from database.services.seed_service import SeedService

SeedService.ensure_bootstrap_data()
print("seed ok")
