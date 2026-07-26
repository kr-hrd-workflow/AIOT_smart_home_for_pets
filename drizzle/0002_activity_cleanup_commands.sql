CREATE TABLE `activity_cleanup_commands` (
	`id` text PRIMARY KEY NOT NULL,
	`home_id` text NOT NULL,
	`agent_id` text NOT NULL,
	`type` text NOT NULL CHECK (`type` = 'delete_activity_observations'),
	`status` text NOT NULL CHECK (`status` IN ('pending','acknowledged')),
	`created_at` text NOT NULL,
	`acknowledged_at` text,
	FOREIGN KEY (`home_id`) REFERENCES `homes`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`agent_id`) REFERENCES `agents`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `activity_cleanup_commands_home_id_unique` ON `activity_cleanup_commands` (`home_id`);
--> statement-breakpoint
CREATE INDEX `activity_cleanup_commands_agent_status_idx` ON `activity_cleanup_commands` (`agent_id`,`status`);
