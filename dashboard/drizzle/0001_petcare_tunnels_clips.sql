CREATE TABLE `clip_events` (
	`clip_id` text NOT NULL,
	`event_type` text NOT NULL CHECK (`event_type` IN ('eating','resting','bed_sensor_mismatch')),
	`event_id` text NOT NULL,
	PRIMARY KEY(`clip_id`, `event_type`, `event_id`),
	FOREIGN KEY (`clip_id`) REFERENCES `clips`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `clips` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`camera_id` text NOT NULL,
	`object_key` text NOT NULL,
	`sha256` text NOT NULL,
	`size_bytes` integer NOT NULL,
	`started_at` text NOT NULL,
	`ended_at` text NOT NULL,
	`expires_at` text NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `clips_object_key_unique` ON `clips` (`object_key`);--> statement-breakpoint
CREATE INDEX `clips_home_expires_idx` ON `clips` (`home_id`, `expires_at`);--> statement-breakpoint
CREATE TABLE `object_deletion_jobs` (
	`object_key` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`requested_at` text NOT NULL,
	`last_error` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `object_deletion_jobs_home_idx` ON `object_deletion_jobs` (`home_id`);--> statement-breakpoint
CREATE TABLE `reconcile_state` (
	`name` text PRIMARY KEY NOT NULL,
	`cursor` text,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `request_limits` (
	`subject` text NOT NULL,
	`route` text NOT NULL,
	`window_start` integer NOT NULL,
	`count` integer NOT NULL,
	`expires_at` text NOT NULL,
	PRIMARY KEY(`subject`, `route`, `window_start`)
);
--> statement-breakpoint
CREATE INDEX `request_limits_expires_idx` ON `request_limits` (`expires_at`);--> statement-breakpoint
CREATE TABLE `tenant_cleanup` (
	`owner_sub` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`status` text NOT NULL CHECK (`status` = 'cleanup_pending'),
	`started_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`last_error` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `tenant_cleanup_home_id_unique` ON `tenant_cleanup` (`home_id`);--> statement-breakpoint
CREATE TRIGGER `block_home_recreation_during_petcare_cleanup`
BEFORE INSERT ON `homes`
WHEN EXISTS (SELECT 1 FROM `tenant_cleanup` WHERE `owner_sub` = NEW.`owner_sub`)
BEGIN
	SELECT RAISE(ABORT, 'petcare_cleanup_pending');
END;--> statement-breakpoint
CREATE TABLE `tunnel_routes` (
	`home_id` text PRIMARY KEY NOT NULL,
	`agent_id` text NOT NULL,
	`tunnel_id` text,
	`tunnel_origin` text,
	`access_app_id` text,
	`access_policy_id` text,
	`access_aud` text,
	`dns_record_id` text,
	`activation_expires_at` text,
	`lease_id` text,
	`lease_expires_at` text,
	`status` text NOT NULL CHECK (`status` IN ('provisioning','activation_pending','active','cleanup_pending','revocation_pending','revoked')),
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`last_error` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE TABLE `upload_nonces` (
	`agent_id` text NOT NULL,
	`nonce` text NOT NULL,
	`used_at` text NOT NULL,
	`expires_at` text NOT NULL,
	PRIMARY KEY(`agent_id`, `nonce`),
	UNIQUE (`agent_id`, `nonce`)
);
--> statement-breakpoint
CREATE INDEX `upload_nonces_expires_idx` ON `upload_nonces` (`expires_at`);
