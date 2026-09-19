import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.app.services.discovery import DiscoveryService
from backend.app.services.telemetry import TelemetryService
from backend.app.services.cost_estimator import CostEstimationService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Initialize services
discovery_service = DiscoveryService()
telemetry_service = TelemetryService()
cost_estimation_service = CostEstimationService()

def setup_scheduler():
    """Registers all periodic jobs."""
    logger.info("Setting up APScheduler jobs...")
    
    # Discovery job (every 15 minutes)
    scheduler.add_job(
        discovery_service.run,
        'interval',
        minutes=15,
        id='discovery_job',
        replace_existing=True,
        misfire_grace_time=60
    )
    
    # Telemetry collection (every 5 minutes)
    scheduler.add_job(
        telemetry_service.run,
        'interval',
        minutes=5,
        id='telemetry_job',
        replace_existing=True,
        misfire_grace_time=60
    )
    
    # Cost estimation update (every hour)
    scheduler.add_job(
        cost_estimation_service.run,
        'interval',
        hours=1,
        id='cost_estimation_job',
        replace_existing=True,
        misfire_grace_time=60
    )
    
    # ML and Action verification jobs will be added here later
    
    logger.info("APScheduler jobs configured.")
