CREATE TABLE `__new_agents` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`public_key` text NOT NULL,
	`tunnel_origin` text,
	`connection_mode` text NOT NULL CHECK (`connection_mode` IN ('outbound','tunnel')),
	`last_seen_at` text,
	`revoked_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
INSERT INTO `__new_agents` (`id`, `home_id`, `public_key`, `tunnel_origin`, `connection_mode`, `last_seen_at`, `revoked_at`)
SELECT `id`, `home_id`, `public_key`, `tunnel_origin`, 'tunnel', `last_seen_at`, `revoked_at` FROM `agents`;
--> statement-breakpoint
CREATE TABLE `__new_cameras` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`local_camera_id` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`disabled_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`agent_id`) REFERENCES `__new_agents`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
INSERT INTO `__new_cameras` (`id`, `home_id`, `agent_id`, `local_camera_id`, `created_at`, `disabled_at`)
SELECT `id`, `home_id`, `agent_id`, `local_camera_id`, `created_at`, `disabled_at` FROM `cameras`;
--> statement-breakpoint
CREATE TABLE `__new_activity_cleanup_commands` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`type` text NOT NULL CHECK (`type` = 'delete_activity_observations'),
	`status` text NOT NULL CHECK (`status` IN ('pending','acknowledged')),
	`created_at` text NOT NULL,
	`acknowledged_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`agent_id`) REFERENCES `__new_agents`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
INSERT INTO `__new_activity_cleanup_commands` (`id`, `home_id`, `agent_id`, `type`, `status`, `created_at`, `acknowledged_at`)
SELECT `id`, `home_id`, `agent_id`, `type`, `status`, `created_at`, `acknowledged_at` FROM `activity_cleanup_commands`;
--> statement-breakpoint
DROP TABLE `activity_cleanup_commands`;
--> statement-breakpoint
DROP TABLE `cameras`;
--> statement-breakpoint
DROP TABLE `agents`;
--> statement-breakpoint
ALTER TABLE `__new_agents` RENAME TO `agents`;
--> statement-breakpoint
ALTER TABLE `__new_cameras` RENAME TO `cameras`;
--> statement-breakpoint
ALTER TABLE `__new_activity_cleanup_commands` RENAME TO `activity_cleanup_commands`;
--> statement-breakpoint
CREATE UNIQUE INDEX `agents_one_active_home` ON `agents` (`home_id`) WHERE "agents"."revoked_at" IS NULL;
--> statement-breakpoint
CREATE INDEX `agents_home_idx` ON `agents` (`home_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX `cameras_one_active_home` ON `cameras` (`home_id`) WHERE "cameras"."disabled_at" IS NULL;
--> statement-breakpoint
CREATE INDEX `cameras_agent_idx` ON `cameras` (`agent_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX `activity_cleanup_commands_home_id_unique` ON `activity_cleanup_commands` (`home_id`);
--> statement-breakpoint
CREATE INDEX `activity_cleanup_commands_agent_status_idx` ON `activity_cleanup_commands` (`agent_id`,`status`);
--> statement-breakpoint
CREATE TABLE `agent_snapshots` (
	`home_id` text PRIMARY KEY NOT NULL,
	`agent_id` text NOT NULL UNIQUE,
	`body` text NOT NULL,
	`generated_at` text NOT NULL,
	`received_at` text NOT NULL,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`agent_id`) REFERENCES `agents`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `agent_snapshots_received_idx` ON `agent_snapshots` (`received_at`);
--> statement-breakpoint
CREATE TABLE `live_streams` (
	`home_id` text PRIMARY KEY NOT NULL,
	`agent_id` text NOT NULL UNIQUE,
	`camera_id` text NOT NULL UNIQUE,
	`boot_id` text NOT NULL,
	`init_object_key` text NOT NULL UNIQUE,
	`newest_sequence` integer NOT NULL CHECK (`newest_sequence` >= 0),
	`updated_at` text NOT NULL,
	`expires_at` text NOT NULL,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`agent_id`) REFERENCES `agents`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`camera_id`) REFERENCES `cameras`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `live_streams_expires_idx` ON `live_streams` (`expires_at`);
--> statement-breakpoint
CREATE TABLE `live_parts` (
	`home_id` text NOT NULL,
	`boot_id` text NOT NULL,
	`sequence` integer NOT NULL CHECK (`sequence` >= 0),
	`object_key` text NOT NULL UNIQUE,
	`sha256` text NOT NULL,
	`size_bytes` integer NOT NULL CHECK (`size_bytes` >= 0),
	`started_at` text NOT NULL,
	`duration_ms` integer NOT NULL CHECK (`duration_ms` = 1000),
	`created_at` text NOT NULL,
	`expires_at` text NOT NULL,
	PRIMARY KEY(`home_id`, `boot_id`, `sequence`),
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `live_parts_home_expires_idx` ON `live_parts` (`home_id`, `expires_at`);
