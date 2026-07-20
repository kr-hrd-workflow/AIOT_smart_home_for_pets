CREATE TABLE `agents` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`public_key` text NOT NULL,
	`tunnel_origin` text NOT NULL,
	`last_seen_at` text,
	`revoked_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE UNIQUE INDEX `agents_one_active_home` ON `agents` (`home_id`) WHERE "agents"."revoked_at" IS NULL;--> statement-breakpoint
CREATE INDEX `agents_home_idx` ON `agents` (`home_id`);--> statement-breakpoint
CREATE TABLE `cameras` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`local_camera_id` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`disabled_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`agent_id`) REFERENCES `agents`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE UNIQUE INDEX `cameras_one_active_home` ON `cameras` (`home_id`) WHERE "cameras"."disabled_at" IS NULL;--> statement-breakpoint
CREATE INDEX `cameras_agent_idx` ON `cameras` (`agent_id`);--> statement-breakpoint
CREATE TABLE `enrollment_tokens` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`token_hash` text NOT NULL,
	`expires_at` text NOT NULL,
	`consumed_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `enrollment_tokens_token_hash_unique` ON `enrollment_tokens` (`token_hash`);--> statement-breakpoint
CREATE INDEX `enrollment_tokens_home_idx` ON `enrollment_tokens` (`home_id`);--> statement-breakpoint
CREATE TABLE `homes` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_sub` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`deleted_at` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX `homes_one_active_owner` ON `homes` (`owner_sub`) WHERE "homes"."deleted_at" IS NULL;